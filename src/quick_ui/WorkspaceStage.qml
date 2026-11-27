import QtQuick
import QtQuick.Controls

Item {
    id: stage
    objectName: "pageLoader"
    required property string route
    required property var pageComponents
    property bool reduceMotion: false
    property int maxCachedPages: 4

    readonly property var routeOrder: [
        "processing", "results", "tuning", "tuning_workshop", "chorus",
        "project_convert", "audio_mixdown", "video_mixdown", "publishing",
        "components", "updates"
    ]
    property var lruRoutes: ["processing"]
    readonly property string activeRoute: routeOrder.indexOf(route) >= 0 ? route : "processing"

    readonly property Item item: {
        switch (activeRoute) {
        case "results": return l_results.item
        case "tuning": return l_tuning.item
        case "tuning_workshop": return l_tuning_workshop.item
        case "chorus": return l_chorus.item
        case "project_convert": return l_project_convert.item
        case "audio_mixdown": return l_audio_mixdown.item
        case "video_mixdown": return l_video_mixdown.item
        case "publishing": return l_publishing.item
        case "components": return l_components.item
        case "updates": return l_updates.item
        default: return l_processing.item
        }
    }

    function touchRoute(target) {
        const key = routeOrder.indexOf(target) >= 0 ? target : "processing"
        const next = lruRoutes.filter(r => r !== key)
        next.push(key)
        while (next.length > Math.max(1, maxCachedPages))
            next.shift()
        lruRoutes = next
    }

    function isCached(routeId) {
        return routeId === activeRoute || lruRoutes.indexOf(routeId) >= 0
    }

    onActiveRouteChanged: {
        touchRoute(activeRoute)
        if (!reduceMotion && item) {
            fadeAnim.stop()
            item.opacity = 0.84
            fadeAnim.target = item
            fadeAnim.start()
        }
    }

    Component.onCompleted: touchRoute(activeRoute)

    NumberAnimation {
        id: fadeAnim
        property: "opacity"
        from: 0.84
        to: 1.0
        duration: 90
        easing.type: Easing.OutCubic
    }

    Loader { id: l_processing; anchors.fill: parent; active: stage.isCached("processing"); visible: stage.activeRoute === "processing"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.processing }
    Loader { id: l_results; anchors.fill: parent; active: stage.isCached("results"); visible: stage.activeRoute === "results"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.results }
    Loader { id: l_tuning; anchors.fill: parent; active: stage.isCached("tuning"); visible: stage.activeRoute === "tuning"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.tuning }
    Loader { id: l_tuning_workshop; anchors.fill: parent; active: stage.isCached("tuning_workshop"); visible: stage.activeRoute === "tuning_workshop"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.tuning_workshop }
    Loader { id: l_chorus; anchors.fill: parent; active: stage.isCached("chorus"); visible: stage.activeRoute === "chorus"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.chorus }
    Loader { id: l_project_convert; anchors.fill: parent; active: stage.isCached("project_convert"); visible: stage.activeRoute === "project_convert"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.project_convert }
    Loader { id: l_audio_mixdown; anchors.fill: parent; active: stage.isCached("audio_mixdown"); visible: stage.activeRoute === "audio_mixdown"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.audio_mixdown }
    Loader { id: l_video_mixdown; anchors.fill: parent; active: stage.isCached("video_mixdown"); visible: stage.activeRoute === "video_mixdown"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.video_mixdown }
    Loader { id: l_publishing; anchors.fill: parent; active: stage.isCached("publishing"); visible: stage.activeRoute === "publishing"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.publishing }
    Loader { id: l_components; anchors.fill: parent; active: stage.isCached("components"); visible: stage.activeRoute === "components"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.components }
    Loader { id: l_updates; anchors.fill: parent; active: stage.isCached("updates"); visible: stage.activeRoute === "updates"; z: visible ? 1 : 0; sourceComponent: stage.pageComponents.updates }
}
