# Experimental singing styles

[English](StylePresets.en.md) | [简体中文](StylePresets.zh-CN.md)

The independent **Tuning workflow** accepts an existing task or imported SVP
and offers **Gentle [Experimental]**, **Sad [Experimental]** and **Bright &
Cheerful [Experimental]**. The default preserves the original performance.
Whole-track mode uses one style; non-overlapping time segments may each choose
one of the three. None stacks on an earlier style candidate. Settings apply only
when the user regenerates a candidate, and opening, restoring or registering an
older project does not rewrite it.

Gentle focuses on vocal texture; Sad focuses on mid-phrase restraint and a
descending release; Bright & Cheerful focuses on clear, crisp entrances. The user
chooses manually: the application does not analyze lyrics or infer emotion. Every
attempt starts from the verified original-performance snapshot. See [Tuning
workflow](TuningWorkflow.en.md) for source snapshots and recovery.

| Style | ID | Public description |
|---|---|---|
| Gentle [Experimental] | `gentle` | Vocal texture: lighter entrances, a stable voiced core, and a softened descending release. |
| Sad [Experimental] | `sad` | Emotional direction: a clear voiced core, restrained middle phrases, and a slow descending release without turning the whole phrase into extra breathiness. |
| Bright & Cheerful [Experimental] | `bold` | Performance approach: quick entrances, crisp connections, and a solid sustained core. The `bold` recipe values stay unchanged; only the public name changes. |

These descriptions identify draft directions awaiting audition. They are not
measured voicebank results, listening guarantees or physical voice categories.
The recipes are manually authored, not learned corpus coefficients, automatic
semantic classifiers or calibrated responses from a target voicebank.
Brightness, breathiness and distortion risk vary with the voicebank, version and
settings. The user must synthesize and audition the result.

On screen, styles keep fixed identifying colors: `gentle` is soft teal, `sad` is
purple and `bold` is bright, cheerful orange. The color is bound to the style ID,
not to segment creation order, list position or selection state. A segment uses
that same style color in its style button, segment strip, range background and
time selection.

## Segment strength and detail

Overall application strength remains one value. The added `style_detail` record
contains four segment coefficients:

| Field | Applies to |
|---|---|
| `onset` | Timbre and added-pitch onset |
| `sustain` | Timbre only; sustained core |
| `connection` | Added pitch only; local adjacent-note connection |
| `release` | Timbre and added-pitch release |

Each coefficient is in `0…1`. The proposed change is scaled by overall strength
multiplied by that segment coefficient. Missing fields, including in legacy
records, are filled with `1`. Each coefficient applies only to the targets in
the table. Disabling added pitch removes the `connection` pitch gesture and the
pitch parts of `onset` and `release`, while their timbre parts and `sustain`
remain active.

Timbre uses Loudness, Tension, Breathiness and, where applicable, Voicing with
smooth additive changes at the onset, sustained core and release. Pitch adds
bounded gestures at the onset, adjacent-note connections and release. The
existing curves remain the baseline; the style is not presented as the original
automation. Notes shorter than 160 ms, rap/BR material, silence and overlapping
notes receive no new style gestures. Notes, lyrics, timing, voice settings,
retakes, accompaniment, unknown controls and unselected tracks are preserved.

Original control-point positions remain. Existing populated nonlinear curves
stay unchanged, with skipped or partial application recorded. If proposed values
could exceed available channel headroom, the proposed increments are reduced in
one shared proportion instead of flattening individual peaks. Style processing
adds or rewrites no vibrato and does not modify `vibratoEnv`, note vibrato
attributes or existing vibrato states.

All values remain manually authored audition drafts. Structural protection,
reproducibility and a successful report do not establish better listening,
absence of artifacts or a statistically measured style effect.

## Whole track and time segments

The Tuning page provides two style-application modes:

- **Whole track** applies one of the three visible style cards once to the
  selected actual target tracks.
- **Time segments** selected in **Style & curves** show a large, prominent
  **Start editing** button. The button opens the full-screen editor, which shows
  editable segment strips on the fixed ruler above a read-only note view of the
  selected actual target tracks. Each non-overlapping segment keeps its own
  style, overall strength, four detail coefficients and added-pitch switch.

When **Time segments** is already selected, importing an SVP keeps that mode
selected. The **Start editing** entry and its state update immediately.

The editor opens at the first note-bearing part of the project and keeps a
readable phrase-level view with large notes. **Note start** returns to the
earliest note. The editor initially shows **Next segment settings**. Choosing
**Create next segment** while editing an existing segment also returns to this
separate new-drawing tool. Dragging on the ruler or note area and releasing the
mouse creates a new segment with the pending settings; it does not rewrite an
existing segment. A successfully created segment returns the tool automatically
to **Next segment settings**. When no style is selected, VWA keeps the pending
range and asks you to choose a style. A selection shorter than 25 ms or one that
contains no notes is not saved. At most 128 non-overlapping segments are
allowed.

Clicking a segment strip or list row enters **Edit selected segment** and loads
that segment's style, strength, detail and time. The main editor toolbar title
switches between **Next segment settings** and **Edit selected segment** to show
the current new/edit intent. Changes to the selected segment are saved
automatically. **Create next segment** returns to the separate new-drawing
tool. While **Next segment settings** is active,
changing the style, strength or detail updates only the pending settings for the
next segment. **Switching styles applies only to the next segment; click an
existing segment before editing it.**

When you draw a segment, enter a numeric start or end time, move a segment or
resize an edge, VWA checks all actual target tracks. A boundary that enters a
note is expanded outward to that note's complete start or end. Overlapping notes
on multiple actual target tracks are included together, covering every
intersecting note. VWA saves the segment only if the complete alignment still
does not overlap an adjacent segment. If it cannot preserve complete notes
without overlap, it rejects the change and asks you to adjust the range.

Boundary alignment changes only the segment range. It does not shorten, copy or
split the source `notes`, and generated style curves still cover the complete
aligned segment range. Legacy segment drafts remain loadable. If a saved boundary
falls inside a note, generation requires you to drag the edge or change the
start/end time to realign it; old boundaries are not rewritten automatically.

Click a segment strip to select it; drag its middle to move it or either edge to
change its range. Selected segments use larger edge handles: ◀ appears on the
left, ▶ on the right, and ↔ is shown as a resize cue. **Delete**, **Clear**,
**Undo** and **Redo** changes are also saved. Esc cancels the current drag or
clears the selection; Delete removes the selected segment. Dragging a segment
near either view edge auto-scrolls it; releasing commits one range update.

A thicker horizontal scrollbar and explicit **Look left** and **Look right**
buttons remain visible below the view. **Pan view** makes a left-button drag pan
the canvas without creating a segment; choose **Create next segment** to return
to drawing. Middle-drag also pans, Shift+wheel scrolls horizontally and Ctrl+wheel
zooms around the pointer. **Full project** and **Selection** refit the view. The
default piano roll uses 40 px note rows (notes paint at about 36 px), a 260 px/s
time scale and 11.5 pt lyric and piano-key labels. **Row height** changes only
the display between 80%, 100%, 125% and 150%. **Readable** restores the default
horizontal scale and centers the selection or current time.

When the segment list is nonempty, execution reads only that list. Each segment
can use its own `gentle`/`sad`/`bold`, overall strength, four coefficients and
pitch switch within its start/end time. Outside a segment, the style line
produced by the primary parameter source remains. Reference, calibration or
retained primary processing still covers the entire selected actual target
tracks. Every candidate starts from the fixed original performance rather than
stacking on an existing style candidate.

`style_regions` and editor settings are isolated by the input SVP SHA and stored
as a local draft; they do not enter general workflow preferences. The draft
stores current-tool segment settings and separate `next_tool` pending settings.
Legacy drafts remain compatible with their earlier single-settings structure.
Reloading the same input project restores its previous segment draft, including
the current editing state and next-segment settings. Another input uses only its
own draft, so a draft is never applied to a different project. Restoring a
processing record still requires a matching input SHA. The draft only lets the
editor continue; it does not overwrite the source SVP, process or synthesize
audio, export a finished file, or represent a generated result. The separate
**Generate independent tuning project** button creates the tuning project copy;
open that project in SV after generation. Segment processing does not change
notes, syllables, lyrics, voicebank settings or timing.

Choose **Time segments** in **Style & curves** to reveal the large **Start
editing** button. F11 can also open or exit the editor. The **Segment settings**
panel on the right can be shown or hidden and starts collapsed on narrow screens.
Exiting returns to the original page and leaves only the entry; the same
controls, segment data, selection and undo history are retained. The interface
follows VWA's light or dark theme, while each style keeps its fixed identifying
color described above. The view edits only style-segment ranges; it does not
edit source notes, lyrics, voicebank settings or pitch curves, play audio, or
synthesize a voicebank.

## Pitch defaults and compatibility mapping

The `gentle`, `sad` and `bold` IDs enable added local pitch by default; an
explicit off choice is retained. Opening an older project or candidate does not
rewrite its stored settings or candidate file. A restored legacy ID is displayed
with its mapped card. Saved overall strength and an explicit
`style_pitch=False` are retained; when `style_pitch` is absent, the original
legacy ID's default applies. Later preference saves and regenerated candidates
use `gentle`, `sad` or `bold`.

| Legacy ID | Displayed style |
|---|---|
| `soft_ballad`, `intimate_narration` | `gentle` |
| `melancholy`, `fragile_tender` | `sad` |
| `power_pop`, `bright_joy` | `bold` |

Legacy IDs remain readable from existing tasks and older command-line records;
the six entries are no longer separate UI cards.

## Outputs, receipts and command line

Independent tuning sessions save `outputs/tuned-<signature>.svp` with an adjacent
`.svp.tuning.json` receipt. Compatible legacy task exports preserve `voices.svp`
and add `voices-style-<id>-<signature>.svp`; their old producer receipts become
inactive as before. New receipts, workflow settings and export records store
`style_detail`. A style change generates another candidate, and retries start
from the verified baseline rather than the previous styled candidate. Modified
candidates are preserved before producer recovery. Receipts bind the source hash,
settings, applied channels, skip reasons, headroom and density result. Style
settings do not invalidate ASR or MIDI inference.

For a separate copy of an existing SVP, `--style-detail` accepts the four
coefficients as JSON and `--time-range START END` selects one range in project
seconds:

```powershell
dependencies\vocal2midi\python\python.exe -B style_presets.py A.svp B.svp --preset gentle --style-detail '{"onset":1,"sustain":0.8,"connection":0.6,"release":0.4}' --time-range 12.5 28.0
```

Both options are optional; outside the single CLI range, the original style
line remains. `gentle`, `sad` and `bold` include added local pitch by default;
use `--no-style-pitch` to stop pitch while retaining timbre segments. Legacy IDs
remain accepted with their historical recipes and defaults. Complete-note alignment, empty-selection rejection and adjacent-segment checks are editor policies. The core CLI's `--time-range START END` API is unchanged and continues to use the supplied time window.

The standalone command refuses to overwrite the source or a different existing
candidate. It writes a `.svp.style.json` receipt and uses that adjacent receipt
to refuse a known candidate as its next input. Keep the receipt when copying a
candidate: the project alone cannot reliably identify its provenance. Always
start from the original performance. The application pipeline selects its
verified baseline independently. Synthesis and listening remain user actions;
this feature does not start voicebank inference or synthesis.

Units and interpolation definitions follow
[Dreamtonics Automation](https://resource.dreamtonics.com/scripting/Automation.html).
Those definitions do not supply this application's style recipes. Directional
references include [slow-song articulation](https://note.com/city_music55/n/nf53d5476edf1),
[local breath and voice technique](https://note.com/hiryukaoku/n/nedd8dbd1acc8),
[a rock-chorus case](https://note.com/in_densetsu/n/n9e434e7d41af), the
[Synthesizer V 2 base-parameter manual](https://sv2.docs.dreamtonics.com/en/parameters),
and a [tearful/bright tuning example](https://note.com/ka_youtyu/n/n17cedba677e5).
These sources provide control semantics and production direction, not the
application's numeric recipes.

Option and numeric inputs ignore wheel changes, even while focused. The wheel
still scrolls pages and browses combo lists without committing a selection;
mouse clicks and keyboard selection remain available.
