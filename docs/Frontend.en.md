# VWA workspace

[English](Frontend.en.md) | [简体中文](Frontend.zh-CN.md) | [User guide](UserGuide.en.md)

Vocaloid Workflow Assistant (VWA) is the current display and package name. Fresh packages start through `VWA.exe`; an eligible upgraded installation may retain a hidden `BVWA.exe` shortcut alias. Task paths and saved settings remain compatible. See the [update protocol](UpdateProtocol.en.md) for legacy compatibility details. This interface work does not replace the Chinese Qwen or Japanese RomajiASR models or change note, pitch or style recipes.

## Moving between workflows

Normal startup uses an ordinary window, capped at 1440×900 and bounded by the available screen. Maximizing enlarges the sidebar, navigation and background illustration while the content layout makes use of the wider area. Overall text sizing remains controlled by the UI scale preference.

The four main destinations are Preparation, Tuning, Video assembly and Publishing. Tasks/results, Models/components and Settings follow them. Each workflow accepts its supported existing inputs; entering a page does not automatically execute earlier steps or change a running request.

Preparation groups the source and output choices compactly. File format labels are short; the save location belongs to the entire task. The lyrics and notes area holds reference text, language, Chinese characters/pinyin, alignment, extraction steps, manual BPM and export options. MIDI processing and editor-project export are separate decisions. Turning an output off retains its selected format preferences. Subtitles still require a real timing source. Device settings remain accessible from the toolbar.

Tuning accepts a task project or imported SVP and creates a separate candidate. Choose the actual tracks, then one parameter source. Reference and retained sources require a mapping for every selected track. Reference strength and style strength are independent. The style presets and optional local pitch expression remain experimental. Original sampling and shape-preserving densification have separate choices; inspect the actual point counts and skipped controls in the result. A saved candidate with pending metadata can be opened, but registration requires verified evidence. Continue the same session to repair compatible missing metadata.

The input card has two main actions: choose a task or open a local project. The task picker reuses the Tasks/results catalogue, including the song/task title, status and recorded update time. Its selected-task summary also shows recorded processing duration when available. If a task contains several SVPs, choose its project in the same dialog. Tasks without an SVP cannot be imported. Full paths and the input fingerprint are available in tooltips; recovery and the save location are under “恢复与保存设置” (Recovery and save settings). Choosing another input does not automatically restore an older session.

Task references across Tuning, Video assembly, Publishing and task-based calibration recovery use this same catalogue and picker. Search by song, task name or BV identifier, then choose the required input. A rename refreshes the displayed identity without changing the saved path or draft. Each destination checks its own requirements: an SVP for Tuning, an original video for Video assembly, source metadata and a description draft for Publishing, or an existing calibration session. Missing inputs disable confirmation with a reason. Cancelling does not change the current selection. Video Mixdown selection and independent local calibration records remain available through their existing file inputs.

Imported track names and note counts use two lines. When no mapping columns are needed, the track column fills the available table width. Wrapped choices reserve their actual height when resizing or changing interface scale. Large text can require vertical scrolling; controls are not compressed to fit a fixed-height card.

Video assembly accepts independent local video and Mixdown files. Offset is in seconds: positive delays the Mixdown, negative advances it. Automatic alignment disables manual offset. Optional -14 LUFS processing is separate. A static source frame is a preview, not a synchronized audition or a final output. External playback uses the system application. Only the verified formal result can be taken to Publishing; changing inputs invalidates that result selection.

Publishing retains four steps and distinguishes generating materials from opening platform forms. The cover choices are original Bilibili cover, custom image and source-video frame. The original-image preview is distinct from Bilibili's generated 4:3 and 16:9 assets, which fit the whole image with padding. Edits to source, final video, cover or platform copy require regenerating the materials. Sign-in, verification challenges and final publication remain in the user's browser.

## Appearance and input

On a wide preparation workspace, source and separation stay on the left, lyrics and notes use the main central area, and output choices sit at the far right. Smaller windows return to a readable stacked layout. Navigation and the background illustration grow with window size without scaling all content text automatically.

Settings contains light/dark appearance, Moonlight Glass or a solid reading surface, and 100/115/130% interface scaling. Glass here means a cached background inside the application and translucent cards; the window retains its system frame. Native Windows backdrop effects are not enabled. The pale headphone illustration is enlarged and composed directly into the default background, without a separate character switch or layout slot. Solid mode skips all decorative artwork. Missing artwork falls back to the procedural background. See [art assets](ArtAssets.en.md) for provenance.

Radio cards are exclusive choices; checked controls are multiple selections or independent options. The full card is clickable and keyboard focus remains visible. Scroll wheels scroll the page without changing selectors or numeric values. Narrow windows stack columns and keep the main action reachable; scrolling is expected at larger text sizes. Appearance settings merge with existing workspace preferences rather than replacing tuning sessions or output locations.

## State, recovery and limits

Preparation always shows its progress bar, including the idle state. It reflects completed processing stages; the adjacent text gives the current stage and available substage counts. Tuning uses the same thin bar style and a compact phase display rather than a fifth setup step. Its visual feedback advances quickly at first and then slows, stays below completion while work is pending, and displays no numeric percentage. Phase text follows actual worker messages and results. Only a complete verified result fills the bar. Detailed receipts and processing explanations are expandable, and the existing registration checks still apply.

Partial application, pending receipts and failed processing are different states. Read their details before registering or reusing a result. Closing a window, cleaning caches, installing components, renaming/deleting tasks and applying updates remain subject to live resource guards. A publication browser can still be using resources after preparation has finished. Automatic updates remain startup-only.

Offscreen layout and event tests do not establish system-DPI behavior, accessibility-tool compatibility, native material support or media codec coverage. Rendering, acoustic evaluation and listening are separate from frontend tests. The current development interface is not a new published release; test it before packaging or distribution.
