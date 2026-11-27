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
        headphones: "M4 13 V11 C4 6.6 7.6 3 12 3 C16.4 3 20 6.6 20 11 V13 M7 13 H4 V18 A2 2 0 0 0 6 20 H8 V14 A1 1 0 0 0 7 13 Z M17 13 H20 V18 A2 2 0 0 1 18 20 H16 V14 A1 1 0 0 1 17 13 Z",
        plus: "M12 5 V19 M5 12 H19",
        sliders: "M4 6 H8 M12 6 H20 M4 12 H14 M18 12 H20 M4 18 H7 M11 18 H20 M8 4 V8 H12 V4 Z M14 10 V14 H18 V10 Z M7 16 V20 H11 V16 Z",
        external: "M13 4 H20 V11 M20 4 L11 13 M11 5 H5 V19 H19 V13",
        edit: "M4 20 L5 15 L16 4 L20 8 L9 19 Z M13 7 L17 11",
        lyrics: "M5 5 H19 V19 H5 Z M8 9 H16 M8 13 H16 M8 16 H13",
        issues: "M12 3 L22 20 H2 Z M12 9 V13 M12 16 V16.4",
        eraser: "M3 15 L13 5 Q14 4 15 5 L20 10 Q21 11 20 12 L13 19 H7 Z M8 10 L16 17 M13 19 H21",
        check: "M5 12 L10 17 L20 7",
        chevron: "M9 5 L16 12 L9 19",
        // Sidebar page navigation icons (24x24 mono-line SVG family)
        nav_processing: "M2.8 12 A2.2 2.2 0 1 0 7.2 12 A2.2 2.2 0 1 0 2.8 12 M7.2 12 H10.5 C13 12 13 6.5 16 6.5 H16.8 M10.5 12 C13 12 13 17.5 16 17.5 H16.8 M16.8 6.5 A2.2 2.2 0 1 0 21.2 6.5 A2.2 2.2 0 1 0 16.8 6.5 M16.8 17.5 A2.2 2.2 0 1 0 21.2 17.5 A2.2 2.2 0 1 0 16.8 17.5",
        nav_tuning: "M5 4 H19 A2 2 0 0 1 21 6 V18 A2 2 0 0 1 19 20 H5 A2 2 0 0 1 3 18 V6 A2 2 0 0 1 5 4 Z M8 4 V20 M3 9.5 H8 M3 14.5 H8 M10.5 8 H15.5 M13 12 H18.5 M10.5 16 H16.5",
        nav_workshop: "M4 20 L13 11 M16.5 3 Q16.5 7.5 21 7.5 Q16.5 7.5 16.5 12 Q16.5 7.5 12 7.5 Q16.5 7.5 16.5 3 Z M7.5 4.8 V8.2 M5.8 6.5 H9.2 M18.5 15 V18 M17 16.5 H20",
        nav_chorus: "M12 3.5 A3 3 0 0 1 15 6.5 V10.5 A3 3 0 0 1 9 10.5 V6.5 A3 3 0 0 1 12 3.5 Z M6 10.5 A6 6 0 0 0 18 10.5 M12 16.5 V20.5 M8.5 20.5 H15.5",
        nav_mixdown: "M6 4 V8 M6 12 V20 M3.8 10 H8.2 M12 4 V13 M12 17 V20 M9.8 15 H14.2 M18 4 V6 M18 10 V20 M15.8 8 H20.2",
        nav_video: "M5 5 H19 A2 2 0 0 1 21 7 V17 A2 2 0 0 1 19 19 H5 A2 2 0 0 1 3 17 V7 A2 2 0 0 1 5 5 Z M10 9.2 L15.5 12 L10 14.8 Z",
        nav_publishing: "M20.5 3.5 L10.5 13.5 M20.5 3.5 L14 20.5 L10.5 13.5 L3.5 10 Z",
        nav_convert: "M4 8.5 H19 M15 4.5 L19.5 8.5 L15 12.5 M20 15.5 H5 M9 11.5 L4.5 15.5 L9 19.5",
        nav_components: "M12 3 L20 7.5 V16.5 L12 21 L4 16.5 V7.5 Z M12 12 L20 7.5 M12 12 L4 7.5 M12 12 V21",
        nav_results: "M4 6.5 L5.8 8.3 L9.2 4.8 M12 6.8 H20 M4 12.5 L5.8 14.3 L9.2 10.8 M12 12.8 H20 M4.5 18.5 H8.5 M12 18.5 H18.5",
        nav_settings: "M9.6 12 A2.4 2.4 0 1 0 14.4 12 A2.4 2.4 0 1 0 9.6 12 M6.6 12 A5.4 5.4 0 1 0 17.4 12 A5.4 5.4 0 1 0 6.6 12 M12 6.4 V3.7 M12 17.6 V20.3 M6.4 12 H3.7 M17.6 12 H20.3 M16 8 L17.9 6.1 M8 8 L6.1 6.1 M16 16 L17.9 17.9 M8 16 L6.1 17.9",
        // Functional landmark & mode icons (same 24x24 mono-line SVG family)
        music_note: "M8.9 18 V6 L20 4 V16 M4.5 18 A2.2 2.2 0 1 0 8.9 18 A2.2 2.2 0 1 0 4.5 18 M15.6 16 A2.2 2.2 0 1 0 20 16 A2.2 2.2 0 1 0 15.6 16",
        target: "M12 3 A9 9 0 1 0 12 21 A9 9 0 1 0 12 3 M12 7.5 A4.5 4.5 0 1 0 12 16.5 A4.5 4.5 0 1 0 12 7.5 M12 11 A1 1 0 1 0 12 13 A1 1 0 1 0 12 11",
        waveform: "M3 12 H5.5 L7.5 6 L10.5 18 L13.5 5 L16 15 L18.5 10 L20 12 H21",
        align_tracks: "M5 4 V20 M9 7 H19 M9 12 H15 M9 17 H17",
        pitch_curve: "M3 17 C6.5 17 7 8 11 8 C15 8 14.5 15 18.5 15 C20 15 20.5 13 21 12 M9.5 8 A1.5 1.5 0 1 0 12.5 8 A1.5 1.5 0 1 0 9.5 8",
        dynamics: "M4 4 V20 H20 M4 20 L12 12 L20 9 M12 12 V20",
        reverb: "M4 12 A2 2 0 1 0 8 12 A2 2 0 1 0 4 12 M11.5 8.5 A5 5 0 0 1 11.5 15.5 M14.5 6 A8.5 8.5 0 0 1 14.5 18 M17.5 3.5 A12 12 0 0 1 17.5 20.5",
        export_file: "M12 4 V15 M8 8 L12 4 L16 8 M4 15 V19 A1.5 1.5 0 0 0 5.5 20.5 H18.5 A1.5 1.5 0 0 0 20 19 V15",
        layers: "M12 4 L21 8.5 L12 13 L3 8.5 Z M3 13 L12 17.5 L21 13 M3 17 L12 21.5 L21 17",
        refresh: "M20 12 A8 8 0 1 1 17.6 6.3 M20 4 V9 H15",
        palette: "M12 8 A4 4 0 1 0 12 16 A4 4 0 1 0 12 8 M12 3 V5 M12 19 V21 M3 12 H5 M19 12 H21 M5.6 5.6 L7 7 M17 17 L18.4 18.4 M18.4 5.6 L17 7 M7 17 L5.6 18.4",
        storage: "M4 13 H20 V18 A1.5 1.5 0 0 1 18.5 19.5 H5.5 A1.5 1.5 0 0 1 4 18 Z M4 13 L6.5 5.5 A1.5 1.5 0 0 1 8 4.5 H16 A1.5 1.5 0 0 1 17.5 5.5 L20 13 M16 16.2 H17.2",
        help_circle: "M12 3 A9 9 0 1 0 12 21 A9 9 0 1 0 12 3 M9.5 9.5 A2.5 2.5 0 1 1 13.5 11.5 C12.5 12.2 12 12.8 12 14 M12 17 V17.2",
        info_circle: "M12 3 A9 9 0 1 0 12 21 A9 9 0 1 0 12 3 M12 11 V16.5 M12 7.8 V8",
        arrow_left: "M19 12 H5 M11 6 L5 12 L11 18",
        image_frame: "M5 4 H19 A2 2 0 0 1 21 6 V18 A2 2 0 0 1 19 20 H5 A2 2 0 0 1 3 18 V6 A2 2 0 0 1 5 4 Z M7 9.5 A1.5 1.5 0 1 0 10 9.5 A1.5 1.5 0 1 0 7 9.5 M4 17 L9.5 12 L13.5 15.5 L16.5 13 L20 17",
        save: "M5 4 H16 L20 8 V19 A1 1 0 0 1 19 20 H5 A1 1 0 0 1 4 19 V5 A1 1 0 0 1 5 4 Z M8 4 V9 H15 V4 M8 20 V14 H16 V20"
    })

    Shape {
        width: 24
        height: 24
        antialiasing: true
        preferredRendererType: Shape.CurveRenderer
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
