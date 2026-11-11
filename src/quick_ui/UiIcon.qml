import QtQuick
import QtQuick.Shapes

Item {
    id: icon
    required property string glyph
    required property color tint
    implicitWidth: 18
    implicitHeight: 18
    readonly property var paths: ({
        play: "M7 4 L20 12 L7 20 Z",
        pause: "M7 5 H10 V19 H7 Z M14 5 H17 V19 H14 Z",
        loop: "M3 11 V8 Q3 6 5 6 H19 M16 3 L19 6 L16 9 M21 13 V16 Q21 18 19 18 H5 M8 15 L5 18 L8 21",
        expand: "M9 4 H4 V9 M15 4 H20 V9 M4 15 V20 H9 M20 15 V20 H15",
        restore: "M4 9 H9 V4 M20 9 H15 V4 M4 15 H9 V20 M20 15 H15 V20",
        close: "M6 6 L18 18 M18 6 L6 18",
        folder: "M3 19 V5 H9 L11 8 H21 V19 Z",
        plus: "M12 5 V19 M5 12 H19",
        sliders: "M4 6 H8 M12 6 H20 M4 12 H14 M18 12 H20 M4 18 H7 M11 18 H20 M8 4 V8 H12 V4 Z M14 10 V14 H18 V10 Z M7 16 V20 H11 V16 Z",
        external: "M13 4 H20 V11 M20 4 L11 13 M11 5 H5 V19 H19 V13",
        edit: "M4 20 L5 15 L16 4 L20 8 L9 19 Z M13 7 L17 11",
        check: "M5 12 L10 17 L20 7"
    })

    Shape {
        width: 24
        height: 24
        antialiasing: true
        transform: Scale { xScale: icon.width / 24; yScale: icon.height / 24 }
        ShapePath {
            strokeColor: icon.tint
            strokeWidth: 1.85
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            fillColor: icon.glyph === "play" || icon.glyph === "pause" ? icon.tint : "transparent"
            PathSvg { path: icon.paths[icon.glyph] || "" }
        }
    }
}
