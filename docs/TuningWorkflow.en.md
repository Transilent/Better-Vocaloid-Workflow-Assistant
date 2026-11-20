# Preparation and tuning workflows

[English](TuningWorkflow.en.md) | [简体中文](TuningWorkflow.zh-CN.md)

The main sequence is **Preparation → Tuning → Simple mixing → Video assembly → Publishing**.
Video assembly remains optional; a finished video can go directly to publishing.
Voicebank synthesis and final publication remain explicit user actions.

Preparation accepts online or local music, separation results, lyrics, MIDI and
editor-project export options. It produces the score and source material. Its
saved tuning preferences do not implicitly process the newly prepared project.
When regenerating compatible notes, retaining already authored automation stays
available as preservation rather than a new acoustic fit.

Open a task project or import a Synthesizer V Studio 1/2 SVP, OpenUtau USTX, VOCALOID VPR or VSQX, ACE Studio ACEP, or UFData exchange score. Find the lyric, edit a range in single-track or multitrack view, and save a new file in the same format.

You can preview USTX 0.5 and resave it in OpenUtau before editing. Each format writes to its supported native curves. Reopen and audition the result in the original editor.

## Edit lyrics and notes

Open an editable vocal track in single-track view:

- Double-click a note or select it and press F2 to edit its lyric. Enter saves. Tab and Shift+Tab advance to the next or previous editable lyric. Valid text is saved when you leave the field. Esc cancels.
- To **paste phrase lyrics**, hold Shift and drag across the whole notes of a phrase. Open **Paste phrase lyrics** beside the lyric search and choose **Review assignments** after pasting. Edit individual assignments or keep the original lyric for a note. Review shows missing and extra words, alternate readings, same-track overlaps, and protected pronunciations. Pairing skips notes that mark a slur, breath, or other native control. Apply the phrase in one draft edit and press Ctrl+Z once to undo it.
- You can also select individual notes before pasting. The review includes those notes only. Select a timestamp to see its note in the roll, then choose **Resume lyric review** to return to your edits. Review the assignments again if the selected notes change.
- Use **Check editing issues** beside the search to see same-track overlaps, protected pronunciations, and the latest failed edit. Select an issue to locate its note.
- Single-track view opens in **Edit notes**. Drag a note to move or transpose it, or drag its right edge to resize it. Click a note to enter its pitch, start, and end in the inspector. Drag from empty space to select notes. Ctrl-click toggles a note and Shift-click selects consecutive notes. Drag the selection to move or resize it as a group. Up and Down transpose by a semitone. Hold Ctrl to transpose by octaves.
- Left and Right select adjacent notes and bring them into view. Use Ctrl+F to search lyrics, then Enter to jump to a match. Middle-drag pans the roll or parameter lane. Scroll to zoom, hold Shift to pan horizontally, or hold Alt to scroll through pitches. Hold Alt while dragging notes to bypass snapping. Esc cancels an in-progress gesture. Ctrl+Z undoes and Ctrl+Y or Ctrl+Shift+Z redoes a complete edit.
- Choose **Style range** before dragging to mark a style region. In multitrack view you can drag directly in a track lane. Regions apply to the checked tracks. With automatic application enabled, release to save a region to the draft. Shift-drag selects time only. Click a colored region above the piano roll to edit its settings in the inspector. Choose **New region** to configure the next region.
- **Save:** edits are saved to the project draft. Preview the changes, then generate and save a new copy in the original format. A lyric or note edit alone writes only those changes. Existing style regions follow their original notes, and previous candidates are retained and marked out of date.

Existing notes can be edited in USTX 0.6–0.10, supported SVP/SV2 groups, VOCALOID 5/6 VPR, VOCALOID 3/4 VSQX, ACEP (JSON/Zstd, project schema 9 revision 0), and UFData v1. Shared, transposed, or cropped groups and native curves can restrict specific edits. The editor shows the reason. Moves and resizes stay within clips and cannot create same-track overlaps. Use **Keep original performance** while editing.

Select one note in **Edit notes** to see its original pronunciation and available native properties in the inspector. These include fine pitch for SVP, fine pitch and vibrato for USTX, and consonant velocity (VEL) for standard VPR and VSQX. Set the vibrato span to 0% to turn it off. A shorter period produces faster vibrato. Higher VEL values shorten the consonant. Press Enter to apply a valid value.

- **VPR:** a lyric change also writes the pronunciation for the original language. Use Japanese kana or its romaji, an unambiguous Mandarin character or untoned pinyin, or a word with a known pronunciation on the same track in the same language. Locked pronunciation stays intact. Where phoneme positions are stored, the new pronunciation must use the same number of phonemes. Native direct pitch stays protected.
- **VSQX** uses the same pronunciation rules as VPR. Automation that changes over time, singer changes, and authored timing can lock a note's position and duration. Save as a new `.vsqx` in the original version. The saved copy points to the original audio on this computer. Relink or import the backing audio in the original editor when moving to another computer.
- **ACEP:** custom pronunciation, frozen syllables, and manual timing remain protected. Clip edits respect the original crop. ACEP2 remains read-only; export UFData from ACE Studio to edit the exchange score instead.
- **UFData:** existing phonemes are preserved. Notes can move or resize only in gaps between existing pitch data. Absolute pitch data protects the note's key where it overlaps; relative pitch offsets are preserved when changing the key.

## Combine style and drawn pitch

Open **Parameter lane** beside the lyric search to draw lines immediately. Both views share the time viewport, selection, and cursor. The 1/4, 1/8, and 1/16 grids follow the project's tempo changes. Notes can snap to beats. Drawn pitch follows the native time resolution, with 10-cent steps when Snap is enabled.

Show the **Project**, **Style**, and drawn pitch curves individually. The project curve updates with draft fine pitch and vibrato. Edit within ±200 cents or choose **Inspect** to see the full span of the visible curves.

- Use **Curve** to draw a smooth offset across notes in a single drag. The preview highlights editable spans and skips gaps and protected sections on release. Hold Alt for one-cent steps.
- Use **Line** to connect two points within a note. In **Nodes**, click the drawn line to insert a point, drag a point to adjust it, or right-click to delete one. Hold Shift to drag a node at a fixed time.
- Click with **Eraser** to remove a stroke or drag across notes to erase a time range. Each completed gesture is one undo step.

Choose **Preview** to recalculate the style curve after changing the notes or style. The existing style curve remains available while editing drawn pitch. Preview again before generating a candidate from the latest draft.

In single-track view, click the pencil, draw inside one vocal note, and release to save the stroke to the draft. Choose **Edit notes** to return to note editing. The teal line shows a drawn offset of up to ±200 cents. With Snap on, offsets snap every 10 cents. Hold Alt to bypass snapping. Esc cancels a stroke in progress.

Inside the stroke, the drawn offset replaces the style pitch offset and is added to the original pitch curve. The native vibrato and the style's other controls stay in place, with short transitions at the ends of the stroke. Changing the style or strength leaves the stroke alone. The line represents the edit, not synthesized pitch.

Click the stroke's time position with the eraser to remove the whole stroke, or drag to remove part of it. This reveals the existing style and source curve. Draw, erase, note and style edits share undo. Strokes follow their notes when moved; erase any out-of-bounds section before shortening a note past it.

Drawing writes to supported USTX PITD, linear SVP pitchDelta, VPR and VSQX PIT pitch bend, ACEP data-form pitch offsets, and absolute or relative pitch in UFData v1. VPR and VSQX keep their existing pitch bend sensitivity. Reduce the offset if it exceeds that range. ACEP2, direct pitch anchors, and unsupported curve types stay protected. If a stroke or its transition crosses a protected pitch break, the preview asks you to adjust it before saving a new copy in the original format.

## Reference audio and retained parameters

In **Source**, select **Reference audio** and assign an aligned dry vocal to each target track. Begin the recording at the project's time origin. Choose the supported controls and strength, then preview, generate, and save a copy. Preview checks track bindings. Generation analyzes the audio and writes the native curves. Native adapters apply bounded offsets inside the analyzed notes and return to the original values at the boundaries. The receipt lists the channels written and skipped.

| Project | Reference audio | Retained curves |
| --- | --- | --- |
| Eligible SVP/SV2 main groups | Dynamics, tension, breathiness, voicing, and compatible measured calibration | Authored curves and matching expression settings |
| Standard VPR | DYN dynamics and BRE breathiness | DYN and BRE |
| VPR AI | DYN dynamics | DYN |
| VSQX 3/4 | DYN dynamics and BRE breathiness | DYN and BRE |
| USTX 0.6–0.10 | DYN dynamics | DYN |
| Writable ACEP | Energy, tension, and breathiness | Supported sampled Energy, Tension, Breathiness, and Falsetto |
| UFData v1 | — (pitch-only format) | Supported native pitch |

Reference tension and breathiness use the WORLD analyzer in **Models and components**.

For **Retain parameters**, choose a project in the same format and select the original vocal track. The first vocal track is index 0. On the native adapters, the source and target timelines, clips, note positions, durations, and keys must match. The operation copies supported curves while preserving the target score and voicebank. ACEP2 supports browsing. Edit lyrics, notes, or drawn pitch in **Keep original performance**, then save a new input to use with reference audio.

## ACE Studio preview

- **ACEP (JSON/Zstd, project schema 9 revision 0):** edit existing notes and combine style and drawn pitch offsets in selected vocal clips in **Keep original performance** mode. Unedited notes, singers, effects, other controls, and media references are preserved. Clips with native pitch anchors, shared or overlapping notes, or a crop through a note are protected. Use **Save ACEP as…** to write a new file beside the source, where ACE Studio can still resolve `Samples`; then open the copy in ACE Studio.
- **ACEP2 and other unverified ACEP variants:** supported structures can be browsed. Unknown containers and tempo ramps show a UFData fallback. ACEP2 writes are disabled.
- **UFData:** choose UFData in ACE Studio's **Export MIDI** dialog. Edit the notes or apply local pitch changes in VWA, then import the saved UFData into the original ACE Studio project. This route does not carry the original singers, backing audio, effects, or ACE-specific expression. Confirm what pitch data your editor version imports before relying on the result.

[ACE Studio import](https://docs.acestudio.ai/project/file-management/import-files) · [Export](https://docs.acestudio.ai/project/file-management/export-files) · [Project files and Samples](https://docs.acestudio.ai/project/file-management)

## One expression source

Choose one primary source for an SVP that supports these options. For other formats, use the available choices in Tuning:

- **Preserve** keeps authored parameters.
- **Reference mapping** creates the selected Loudness/Tension/Breathiness/Voicing
  draft from explicitly associated vocal audio. This is not voice cloning.
- **Measured calibration** uses a profile from the same score, bank and render
  setup. It requires the existing user-rendered probes; no synthesis happens
  automatically. A partial profile cannot silently erase authored channels that
  were not measured.
- **Retain automation** transfers compatible same-timeline expression from a
  prior project. It is not a new acoustic fit.

Independent retention requires a complete, unique one-to-one match of note
onset, duration and pitch. Missing, extra, ambiguous or shifted notes stop the
transaction. Finite authored values are not discarded simply for exceeding the
solver's nominal range. Legacy file and task-regeneration routes explicitly use
their historical tolerance, still require complete one-to-one coverage, and
record every displacement rather than overwriting a target note twice.

Reference mapping stops if the selected channel has a nonzero voice or note
offset whose baseline cannot be inferred safely. Preserve the performance or
measure a fresh calibration instead of silently stacking new curves on old
biases. Multiple tracks may share a reference role: their caches use actual
track indices. Each measured profile must still match its selected score and
bank settings.

These sources are mutually exclusive. An optional style overlay follows the
primary stage. The unified core then performs shape-preserving densification
once and validates protected project content before a single candidate commit.
If a calibrated candidate also receives style, predicted calibration metrics
describe the primary stage only, not the final styled performance.

The source object is never edited. Preserve, style and density paths retain
original control-point coordinates; style records deliberate value changes.
Reference and calibration explicitly replace selected channels and record their
before/after hashes and point counts; original curves remain recoverable from
the input snapshot. Finite fractional coordinates keep their original types
rather than being truncated. Densification preserves the source function;
adding points is distinct from changing expression. Unknown controls, retakes,
and unselected tracks remain protected. The score changes only through explicit
note edits. Existing nonlinear curves that cannot be processed safely are kept
and reported as partial results.

## Recovery and provenance

A session snapshots the source bytes in `inputs/source.<format>`. Every attempt starts
from that snapshot, including retries, style changes and cache misses. It never
uses the last candidate as its next baseline. Changed settings produce another
signature-bound output. A valid cached result is verified without another
project commit. Modified candidates and damaged receipts are preserved before
recovery. Cancellation and validation failures before commit leave the source
and prior outputs intact.

An output that has already committed is kept even if its receipt or a progress
notification fails. A missing receipt is shown as pending; repeat the same
attempt to repair the hash-bound metadata without applying tuning again. The
candidate can be opened for review, but registration requires a verified
receipt. Source, settings, dependencies or externally edited output changes
prevent metadata-only recovery. Calibration uses the same boundary: a saved
candidate with a missing fit report offers **补全校准报告** (Repair calibration
report), including after reopening the session. An unverified report does not
activate a new calibration profile.

If the original project still exists but changed externally, explicitly reimport
the new version into a new session. If the original is unavailable, a verified
snapshot remains usable. Reference audio, profiles, probe renders, retained
projects and contributing code are hashed for cache validity and checked again
before commit. Concurrent runs of one session are excluded.

Outputs live in `outputs/tuned-<signature>.svp`, with adjacent `.svp.tuning.json`
receipts and session status/options files. Relative accompaniment paths are
resolved to the same original media, without changing the audio or source
snapshot. Keep those referenced media files available. Registering a result as a
task's selected tuned project is a separate user action; generation does not
replace publishing selections.

The file wrappers remain available for existing requests and calibration
sessions. Legacy requests containing multiple primary sources use a recorded
priority: measured calibration, retained automation, then reference mapping.
They no longer apply all three to the same channels sequentially. New independent
tuning options reject such conflicts directly.

## Programmatic use

Run from the application root using the bundled Python. Canonical source is in
`src/`; the root modules are generated runtime copies.

```python
from tuning_workflow import create_session, run_session

session = create_session("song.svp", "new-tuning-session")
result = run_session(session, {
    "expression_mode": "preserve",
    "track_indices": [0],
    "performance_style": "power_pop",
    "style_strength": 0.6,
    "style_pitch": True,
    "expression_density": "dense",
})
print(result["output"])
```

Existing sessions can also run through `tuning_workflow.py --session PATH
--options options.json`. See [style presets](StylePresets.en.md) for experimental
recipe limits. Structural validation and reproducible processing do not establish
acoustic improvement or freedom from audible artifacts; audition the result in
the chosen voicebank.
