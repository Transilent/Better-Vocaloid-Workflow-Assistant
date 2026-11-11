import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "startupDialogs"
    anchors.fill: parent
    required property QtObject controller
    required property QtObject navigationApi
    required property QtObject theme
    z: 10000

    readonly property var snapshot: controller.startup || ({})
    readonly property var routeIds: {
        const routes = navigationApi && navigationApi.routes ? navigationApi.routes : []
        if (routes && routes.length > 0)
            return routes.map(route => String(route.pageId || ""))
        return ["processing", "components", "results", "updates"]
    }
    readonly property var allTourSteps: [
        {
            pageId: "processing",
            title: tr("欢迎与准备"),
            body: tr("从本地音频开始：选择素材，检查分离方案和当前组件状态。准备草稿保存在工作区中，切换页面后会继续保留。")
        },
        {
            pageId: "components",
            title: tr("模型与组件"),
            body: tr("在这里检查分离模型和本地组件。安装或校验时可以切到其他页面，返回后还能查看进度。")
        },
        {
            pageId: "results",
            title: tr("任务与结果"),
            body: tr("在这里查找任务和已核验的文件。登记混音后可继续合成视频，登记视频后可准备封面、文案和发布资料。")
        },
        {
            pageId: "updates",
            title: tr("设置与支持"),
            body: tr("设置页集中管理外观、缩放、语言、下载来源、任务目录和程序更新。需要帮助时，可以复制用户群号、提交 GitHub 反馈或重新查看本教程。")
        }
    ]
    readonly property var tourSteps: allTourSteps.filter(step => routeIds.indexOf(step.pageId) >= 0)
    readonly property int currentTourStep: {
        const requested = Number(snapshot.tour_step || 0)
        if (!tourSteps.length)
            return 0
        return Math.max(0, Math.min(tourSteps.length - 1, isFinite(requested) ? Math.trunc(requested) : 0))
    }
    readonly property var currentStepData: tourSteps.length > 0 ? tourSteps[currentTourStep] : ({})
    readonly property bool finalTourStep: tourSteps.length > 0 && currentTourStep >= tourSteps.length - 1
    readonly property bool updateWanted: !!snapshot.update_open && !snapshot.tour_open
    readonly property string updateState: String(snapshot.update_state || "idle").toLowerCase()
    readonly property string communityNumber: controller.qqGroup || "490619616"
    readonly property bool workBusy: !!controller.workBusy

    property bool syncing: false
    property bool tutorialWasOpen: false
    property bool suppressTutorialReject: false
    property bool suppressUpdateReject: false
    property string tutorialReturnRoute: ""

    function tr(source) {
        return controller && typeof controller.text === "function" ? controller.text(source) : source
    }

    function formatVersion(value) {
        const version = String(value || "").trim()
        if (version.length === 0)
            return tr("未知")
        return version.charAt(0).toLowerCase() === "v" ? version : "v" + version
    }

    function hasRoute(pageId) {
        return routeIds.indexOf(String(pageId || "")) >= 0
    }

    function stepCounter(index, total) {
        return tr("第 {0} / {1} 步").replace("{0}", String(index + 1)).replace("{1}", String(total))
    }

    function delayOptions() {
        return [
            { label: tr("下次启动"), value: 0 },
            { label: tr("一小时后"), value: 3600 },
            { label: tr("一天后"), value: 86400 },
            { label: tr("七天后"), value: 604800 },
            { label: tr("三十天后"), value: 2592000 },
            { label: tr("忽略此版本"), value: -1 }
        ]
    }

    function updateStateLabel(state) {
        switch (String(state || "idle").toLowerCase()) {
        case "checking": return tr("正在检查更新")
        case "available":
        case "update_available": return tr("发现新版本")
        case "downloading": return tr("正在下载更新")
        case "ready":
        case "downloaded": return tr("更新已下载并校验")
        case "applying": return tr("正在重启并安装")
        case "current":
        case "up_to_date": return tr("已是最新版本")
        case "failed":
        case "error": return tr("更新检查失败")
        case "cancelled": return tr("更新已暂缓")
        default: return tr("程序更新")
        }
    }

    function updateStateColor(state) {
        switch (String(state || "idle").toLowerCase()) {
        case "available":
        case "update_available":
        case "ready":
        case "downloaded": return theme.accent
        case "current":
        case "up_to_date": return theme.success
        case "failed":
        case "error": return theme.error
        default: return theme.muted
        }
    }

    function updateMessage() {
        if (String(snapshot.message || "").trim().length > 0)
            return String(snapshot.message)
        if (snapshot.apply_ready)
            return tr("更新包已下载并校验。确认后会重启应用并完成安装。")
        if (snapshot.can_update)
            return tr("此版本支持一键更新。确认后才会开始下载，下载完成前不会替换程序文件。")
        if (updateState === "current" || updateState === "up_to_date")
            return tr("当前版本已是最新版本。")
        if (String(snapshot.url || "").length > 0)
            return tr("此版本不支持一键更新。可以打开正式发布页，按页面说明手动下载安装。")
        return tr("暂无更多更新状态。")
    }

    function updatePrimaryLabel() {
        if (snapshot.apply_ready)
            return tr("重启并安装")
        if (updateState === "downloading" || updateState === "applying")
            return updateStateLabel(updateState)
        if (snapshot.can_update)
            return tr("立即更新")
        if (String(snapshot.url || "").length > 0)
            return tr("打开发布页")
        return tr("立即更新")
    }

    function updatePrimaryEnabled() {
        if (updateState === "downloading" || updateState === "applying")
            return false
        if (workBusy && snapshot.apply_ready)
            return false
        return !!(snapshot.apply_ready || snapshot.can_update || String(snapshot.url || "").length > 0)
    }

    function requestUpdatePrimary() {
        if (snapshot.apply_ready && !workBusy)
            controller.applyUpdate()
        else if (snapshot.can_update)
            controller.downloadUpdate()
        else if (String(snapshot.url || "").length > 0)
            controller.openRelease()
    }

    function requestDefer(seconds) {
        if (snapshot.update_open)
            controller.deferUpdate(Number(seconds))
    }

    function requestDismiss() {
        if (snapshot.update_open)
            controller.dismissUpdate()
    }

    function requestTutorialAdvance() {
        if (snapshot.tour_open && tourSteps.length > 0)
            controller.advanceTutorial()
    }

    function requestTutorialSkip() {
        if (snapshot.tour_open)
            controller.skipTutorial()
    }

    function currentRoute() {
        return navigationApi ? String(navigationApi.route || "") : ""
    }

    function navigateTo(pageId) {
        if (navigationApi && navigationApi.navigate)
            navigationApi.navigate(pageId)
    }

    function restoreTutorialRoute() {
        if (tutorialReturnRoute.length > 0 && hasRoute(tutorialReturnRoute)
                && currentRoute().length > 0 && currentRoute() !== tutorialReturnRoute)
            navigateTo(tutorialReturnRoute)
        tutorialReturnRoute = ""
    }

    function findDescendant(item, objectName) {
        if (!item)
            return null
        if (String(item.objectName || "") === objectName)
            return item
        const children = item.children || []
        for (let index = 0; index < children.length; ++index) {
            const found = findDescendant(children[index], objectName)
            if (found)
                return found
        }
        return null
    }

    function activateTutorialStep() {
        const step = currentStepData
        if (!snapshot.tour_open || !step || !step.pageId || !hasRoute(step.pageId))
            return
        if (currentRoute().length > 0 && currentRoute() !== step.pageId)
            navigateTo(step.pageId)
        const tutorialContentView = findDescendant(tutorialDialog.contentItem, "tutorialScroll")
        if (tutorialContentView && tutorialContentView.contentItem)
            tutorialContentView.contentItem.contentY = 0
    }

    function focusTutorial() {
        Qt.callLater(function() {
            if (!root.snapshot.tour_open || !tutorialDialog.opened)
                return
            const target = findDescendant(tutorialDialog.contentItem, "tutorialNext")
            if (target)
                target.forceActiveFocus()
        })
    }

    function focusUpdate() {
        Qt.callLater(function() {
            if (!root.snapshot.update_open || !updateDialog.opened)
                return
            const target = findDescendant(updateDialog.contentItem, "updateLater")
            if (target)
                target.forceActiveFocus()
        })
    }

    function syncDialogs() {
        if (syncing)
            return
        syncing = true
        const wantsTutorial = !!snapshot.tour_open
        const wantsUpdate = root.updateWanted
        let openedTutorial = false
        let openedUpdate = false

        if (wantsTutorial && !tutorialWasOpen) {
            tutorialReturnRoute = currentRoute()
            tutorialWasOpen = true
        }
        if (!wantsTutorial && tutorialWasOpen)
            tutorialWasOpen = false

        if (wantsTutorial && !tutorialDialog.opened) {
            tutorialDialog.open()
            openedTutorial = true
        } else if (!wantsTutorial && tutorialDialog.opened) {
            suppressTutorialReject = true
            tutorialDialog.close()
            suppressTutorialReject = false
        }

        if (wantsUpdate && !updateDialog.opened) {
            updateDialog.open()
            openedUpdate = true
        } else if (!wantsUpdate && updateDialog.opened) {
            suppressUpdateReject = true
            updateDialog.close()
            suppressUpdateReject = false
        }

        if (!wantsTutorial && tutorialReturnRoute.length > 0)
            restoreTutorialRoute()

        syncing = false
        if (wantsTutorial)
            Qt.callLater(activateTutorialStep)
        if (openedTutorial)
            focusTutorial()
        if (openedUpdate)
            focusUpdate()
    }


    Connections {
        target: root.controller
        function onStartupChanged() { root.syncDialogs() }
    }
    Component.onCompleted: Qt.callLater(syncDialogs)
    onSnapshotChanged: syncDialogs()

    Dialog {
        id: tutorialDialog
        objectName: "tutorialDialog"
        parent: root
        title: root.tr("新手教程")
        header: null
        modal: true
        focus: true
        dim: true
        closePolicy: Popup.CloseOnEscape
        padding: 0
        width: Math.max(1, Math.min(Math.round(560 * root.theme.scale), root.width - Math.round(24 * root.theme.scale)))
        height: Math.max(1, Math.min(Math.round(560 * root.theme.scale), root.height - Math.round(24 * root.theme.scale)))
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)

        Overlay.modal: Rectangle {
            color: Qt.rgba(root.theme.base.r, root.theme.base.g, root.theme.base.b, 0.86)
        }
        background: Rectangle {
            color: root.theme.surface
            radius: Math.round(11 * root.theme.scale)
            border.color: root.theme.line
        }
        contentItem: Item {
            implicitWidth: Math.round(560 * root.theme.scale)
            implicitHeight: Math.round(560 * root.theme.scale)

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(62 * root.theme.scale)
                    color: root.theme.raised
                    radius: Math.round(11 * root.theme.scale)
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: parent.radius
                        color: parent.color
                    }
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Math.round(18 * root.theme.scale)
                        anchors.rightMargin: Math.round(12 * root.theme.scale)
                        spacing: Math.round(8 * root.theme.scale)
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                text: root.tr("首次使用")
                                color: root.theme.accent
                                font.pixelSize: Math.round(11 * root.theme.scale)
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: root.tr("了解工作区")
                                color: root.theme.text
                                font.pixelSize: Math.round(18 * root.theme.scale)
                                font.weight: Font.DemiBold
                            }
                        }
                        UiButton {
                            id: tutorialSkip
                            objectName: "tutorialSkip"
                            theme: root.theme
                            quiet: true
                            text: root.tr("跳过教程")
                            onClicked: root.requestTutorialSkip()
                        }
                    }
                }

                ScrollView {
                    id: tutorialScroll
                    objectName: "tutorialScroll"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.leftMargin: Math.round(18 * root.theme.scale)
                    Layout.rightMargin: Math.round(18 * root.theme.scale)
                    Layout.topMargin: Math.round(14 * root.theme.scale)
                    contentWidth: availableWidth
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: tutorialScroll.availableWidth
                        spacing: Math.round(10 * root.theme.scale)

                        Text {
                            objectName: "tutorialProgress"
                            text: root.tourSteps.length > 0
                                ? root.stepCounter(root.currentTourStep, root.tourSteps.length)
                                : root.tr("暂无可展示的页面")
                            color: root.theme.muted
                            font.pixelSize: Math.round(11 * root.theme.scale)
                        }
                        Text {
                            objectName: "tutorialTitle"
                            Layout.fillWidth: true
                            text: String(root.currentStepData.title || root.tr("工作区教程"))
                            color: root.theme.text
                            font.pixelSize: Math.round(22 * root.theme.scale)
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                        Text {
                            objectName: "tutorialBody"
                            Layout.fillWidth: true
                            text: String(root.currentStepData.body || "")
                            color: root.theme.muted
                            font.pixelSize: Math.round(14 * root.theme.scale)
                            lineHeight: 1.35
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.topMargin: Math.round(4 * root.theme.scale)
                            Layout.preferredHeight: Math.round(44 * root.theme.scale)
                            color: root.theme.base
                            radius: Math.round(8 * root.theme.scale)
                            border.color: root.theme.line
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Math.round(12 * root.theme.scale)
                                anchors.rightMargin: Math.round(12 * root.theme.scale)
                                spacing: 0
                                Text {
                                    Layout.alignment: Qt.AlignVCenter
                                    text: root.tr("教程会临时切换页面，关闭后返回进入教程前的页面。")
                                    color: root.theme.muted
                                    font.pixelSize: Math.round(11 * root.theme.scale)
                                    wrapMode: Text.Wrap
                                }
                                Item { Layout.fillHeight: true }
                            }
                        }

                        Rectangle {
                            objectName: "tutorialSupport"
                            Layout.fillWidth: true
                            Layout.topMargin: Math.round(4 * root.theme.scale)
                            Layout.bottomMargin: Math.round(6 * root.theme.scale)
                            visible: root.finalTourStep
                            Layout.preferredHeight: supportLayout.implicitHeight + Math.round(22 * root.theme.scale)
                            color: root.theme.base
                            radius: Math.round(8 * root.theme.scale)
                            border.color: root.theme.line
                            ColumnLayout {
                                id: supportLayout
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: Math.round(11 * root.theme.scale)
                                spacing: Math.round(7 * root.theme.scale)
                                Text {
                                    text: root.tr("社区支持")
                                    color: root.theme.text
                                    font.pixelSize: Math.round(12 * root.theme.scale)
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    objectName: "tutorialQQGroup"
                                    Layout.fillWidth: true
                                    text: root.tr("QQ 群") + "  " + root.communityNumber
                                    color: root.theme.muted
                                    font.pixelSize: Math.round(12 * root.theme.scale)
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: Math.round(6 * root.theme.scale)
                                    UiButton {
                                        theme: root.theme
                                        quiet: true
                                        text: root.tr("复制群号")
                                        onClicked: root.controller.copyCommunity()
                                    }
                                    UiButton {
                                        theme: root.theme
                                        quiet: true
                                        text: root.tr("GitHub 反馈")
                                        onClicked: root.controller.openFeedback()
                                    }
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: Math.round(18 * root.theme.scale)
                    Layout.rightMargin: Math.round(18 * root.theme.scale)
                    Layout.topMargin: Math.round(8 * root.theme.scale)
                    Layout.bottomMargin: Math.round(16 * root.theme.scale)
                    spacing: Math.round(8 * root.theme.scale)
                    Repeater {
                        model: root.tourSteps.length
                        Rectangle {
                            width: Math.round(6 * root.theme.scale)
                            height: width
                            radius: width / 2
                            color: index === root.currentTourStep ? root.theme.accent : root.theme.line
                        }
                    }
                    Item { Layout.fillWidth: true }
                    UiButton {
                        objectName: "tutorialNext"
                        theme: root.theme
                        primary: true
                        enabled: root.tourSteps.length > 0
                        text: root.finalTourStep ? root.tr("完成") : root.tr("下一步")
                        onClicked: root.requestTutorialAdvance()
                    }
                }
            }
        }

        onOpened: root.focusTutorial()
        onRejected: {
            if (!root.suppressTutorialReject)
                root.requestTutorialSkip()
        }
    }

    Dialog {
        id: updateDialog
        objectName: "updateNoticeDialog"
        parent: root
        title: root.tr("程序更新")
        header: null
        modal: true
        focus: true
        dim: true
        closePolicy: Popup.CloseOnEscape
        padding: 0
        width: Math.max(1, Math.min(Math.round(650 * root.theme.scale), root.width - Math.round(24 * root.theme.scale)))
        height: Math.max(1, Math.min(Math.round(620 * root.theme.scale), root.height - Math.round(24 * root.theme.scale)))
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)

        Overlay.modal: Rectangle {
            color: Qt.rgba(root.theme.base.r, root.theme.base.g, root.theme.base.b, 0.86)
        }
        background: Rectangle {
            color: root.theme.surface
            radius: Math.round(11 * root.theme.scale)
            border.color: root.theme.line
        }
        contentItem: Item {
            implicitWidth: Math.round(650 * root.theme.scale)
            implicitHeight: Math.round(620 * root.theme.scale)

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: headerRow.implicitHeight + Math.round(24 * root.theme.scale)
                    color: root.theme.raised
                    radius: Math.round(11 * root.theme.scale)
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: parent.radius
                        color: parent.color
                    }
                    RowLayout {
                        id: headerRow
                        anchors.fill: parent
                        anchors.leftMargin: Math.round(18 * root.theme.scale)
                        anchors.rightMargin: Math.round(12 * root.theme.scale)
                        spacing: Math.round(9 * root.theme.scale)
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                Layout.fillWidth: true
                                text: root.updateStateLabel(root.updateState)
                                color: root.updateStateColor(root.updateState)
                                font.pixelSize: Math.round(11 * root.theme.scale)
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            Text {
                                objectName: "updateVersion"
                                Layout.fillWidth: true
                                text: String(root.snapshot.version || "").length > 0
                                    ? root.formatVersion(root.snapshot.version)
                                    : root.tr("程序更新")
                                color: root.theme.text
                                font.pixelSize: Math.round(21 * root.theme.scale)
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                        }
                        UiButton {
                            objectName: "updateClose"
                            theme: root.theme
                            quiet: true
                            text: "×"
                            leftPadding: Math.round(9 * root.theme.scale)
                            rightPadding: Math.round(9 * root.theme.scale)
                            Accessible.name: root.tr("关闭更新提示并下次启动再提醒")
                            onClicked: root.requestDismiss()
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.leftMargin: Math.round(18 * root.theme.scale)
                    Layout.rightMargin: Math.round(18 * root.theme.scale)
                    Layout.topMargin: Math.round(13 * root.theme.scale)
                    Layout.bottomMargin: Math.round(8 * root.theme.scale)
                    spacing: Math.round(9 * root.theme.scale)

                    Text {
                        objectName: "updateCurrentVersion"
                        Layout.fillWidth: true
                        text: root.tr("当前版本") + "  " + root.formatVersion(root.controller.currentVersion)
                        color: root.theme.muted
                        font.pixelSize: Math.round(12 * root.theme.scale)
                    }
                    Text {
                        objectName: "updateCompatibility"
                        Layout.fillWidth: true
                        text: root.updateMessage()
                        color: root.updateState === "failed" || root.updateState === "error"
                            ? root.theme.error : root.theme.muted
                        font.pixelSize: Math.round(12 * root.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * root.theme.scale)
                        Text {
                            text: root.tr("社区支持")
                            color: root.theme.text
                            font.pixelSize: Math.round(12 * root.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            objectName: "updateQQGroup"
                            Layout.fillWidth: true
                            text: root.tr("QQ 群") + "  " + root.communityNumber
                            color: root.theme.muted
                            font.pixelSize: Math.round(12 * root.theme.scale)
                            elide: Text.ElideRight
                        }
                        UiButton {
                            theme: root.theme
                            quiet: true
                            text: root.tr("复制群号")
                            onClicked: root.controller.copyCommunity()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: root.tr("本次变更")
                        color: root.theme.text
                        font.pixelSize: Math.round(12 * root.theme.scale)
                        font.weight: Font.DemiBold
                    }
                    ScrollView {
                        id: updateNotesScroll
                        objectName: "updateNotesScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: Math.round(100 * root.theme.scale)
                        contentWidth: availableWidth
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        background: Rectangle {
                            color: root.theme.base
                            radius: Math.round(7 * root.theme.scale)
                            border.color: root.theme.line
                        }
                        TextArea {
                            id: updateNotes
                            objectName: "updateNotes"
                            width: updateNotesScroll.availableWidth
                            text: String(root.snapshot.notes || "").length > 0
                                ? String(root.snapshot.notes)
                                : root.tr("此版本未提供更新说明。")
                            readOnly: true
                            selectByMouse: true
                            selectByKeyboard: true
                            wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.PlainText
                            color: root.theme.text
                            selectionColor: root.theme.accent
                            selectedTextColor: root.theme.base
                            font.pixelSize: Math.round(12 * root.theme.scale)
                            leftPadding: Math.round(10 * root.theme.scale)
                            rightPadding: Math.round(10 * root.theme.scale)
                            topPadding: Math.round(9 * root.theme.scale)
                            bottomPadding: Math.round(9 * root.theme.scale)
                            background: Rectangle { color: "transparent" }
                            Accessible.name: root.tr("更新说明")
                        }
                    }

                    ProgressBar {
                        id: updateProgress
                        objectName: "updateNoticeProgress"
                        Layout.fillWidth: true
                        visible: root.updateState === "downloading" || Number(root.snapshot.total || 0) > 0
                        indeterminate: root.updateState === "downloading"
                            && Number(root.snapshot.total || 0) <= 0 && !root.theme.reduceMotion
                        value: Number(root.snapshot.total || 0) > 0
                            ? Math.max(0, Math.min(1, Number(root.snapshot.done || 0) / Number(root.snapshot.total)))
                            : 0
                        background: Rectangle {
                            implicitHeight: Math.max(5, Math.round(6 * root.theme.scale))
                            radius: height / 2
                            color: root.theme.base
                        }
                        contentItem: Item {
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, updateProgress.visualPosition))
                                height: parent.height
                                radius: height / 2
                                color: root.updateStateColor(root.updateState)
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Text {
                            text: root.tr("暂不提醒")
                            color: root.theme.muted
                            font.pixelSize: Math.round(12 * root.theme.scale)
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(7 * root.theme.scale)
                            UiComboBox {
                                id: updateDelay
                                objectName: "updateDelayCombo"
                                Layout.fillWidth: true
                                Layout.maximumWidth: Math.round(220 * root.theme.scale)
                                theme: root.theme
                                model: root.delayOptions()
                                currentIndex: 0
                            }
                            UiButton {
                                objectName: "updateLater"
                                theme: root.theme
                                quiet: true
                                text: root.tr("延后提醒")
                                onClicked: root.requestDefer(Number(updateDelay.currentValue))
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: root.workBusy && root.snapshot.apply_ready
                        text: root.tr("当前任务正在运行。更新包会保留，请结束任务后再重启安装。")
                        color: root.theme.accent
                        font.pixelSize: Math.round(11 * root.theme.scale)
                        wrapMode: Text.Wrap
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(1 * root.theme.scale)
                    color: root.theme.line
                }
                Flow {
                    Layout.fillWidth: true
                    Layout.leftMargin: Math.round(18 * root.theme.scale)
                    Layout.rightMargin: Math.round(18 * root.theme.scale)
                    Layout.topMargin: Math.round(10 * root.theme.scale)
                    Layout.bottomMargin: Math.round(16 * root.theme.scale)
                    Layout.alignment: Qt.AlignRight
                    spacing: Math.round(7 * root.theme.scale)
                    UiButton {
                        objectName: "updateCancelDownload"
                        theme: root.theme
                        quiet: true
                        visible: root.updateState === "downloading" && root.snapshot.can_cancel
                        text: root.tr("取消下载")
                        onClicked: root.controller.cancelUpdate()
                    }
                    UiButton {
                        objectName: "updateImmediate"
                        theme: root.theme
                        primary: true
                        enabled: root.updatePrimaryEnabled()
                        text: root.updatePrimaryLabel()
                        onClicked: root.requestUpdatePrimary()
                    }
                }
            }
        }

        onOpened: root.focusUpdate()
        onRejected: {
            if (!root.suppressUpdateReject)
                root.requestDismiss()
        }
    }

}
