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
  **Start editing** button; the main page no longer embeds a separate piano-roll
  card. The button opens the full-screen editor, which shows editable segment
  strips on the fixed ruler above a read-only note view of the selected actual
  target tracks. Drag on the ruler or note area to create a time selection. Click
  a segment strip to select it; drag its middle to move it, or either edge to
  change its range. Selected segments use larger edge handles: ◀ appears on the
  left, ▶ on the right, and ↔ is shown as a resize cue. Esc cancels the current
  drag or clears the selection; Delete removes the selected segment. A clear
  horizontal scrollbar and **Move view left** and **Move view right** buttons
  remain visible below the view. Use **Full project** and **Selection** to refit
  the view. Ctrl+wheel zooms around the pointer, Shift+wheel scrolls horizontally,
  and dragging with the middle mouse button pans. Dragging a segment near the
  left or right edge auto-scrolls the view; releasing the drag commits one range
  update. **Snap** is off by default; when enabled, it aligns to
  nearby note boundaries and holding Alt turns it off temporarily. This view
  edits only the start and end of style segments. It does not edit source notes,
  lyrics, voicebank settings or pitch curves, play audio, or synthesize a
  voicebank.

In Time segments mode, enter **Start** and **End** in seconds (up to three
decimal places), or create or adjust a time selection in the editor timeline. Then
set the style, overall strength, four segment coefficients and added-pitch
choice, and choose **Add**. Release a dragged segment, or edit the time fields
and choose **Update**, to write its range back. Selecting a list row loads that
segment's settings into the controls. **Delete** and **Clear** manage the current
input; **Undo/Redo** restores segment edits (Ctrl+Z; Ctrl+Y or Ctrl+Shift+Z).
Segments cannot overlap, at most 128 are allowed, and a selection shorter than
25 ms cannot become a segment.

When the segment list is nonempty, execution reads only that list. Each segment
can use its own `gentle`/`sad`/`bold`, overall strength, four coefficients and
pitch switch within its start/end time. Outside a segment, the style line produced by
the primary parameter source remains. In this mode, the style and segment
controls above are tool settings for adding or updating segments; they do not
create an additional whole-track style overlay. Reference, calibration or
retained primary processing still covers the entire selected actual target
tracks. Segments do not overlap, and every candidate starts from the fixed
original performance rather than stacking on an existing style candidate.

`style_regions` is stored only in execution options for the current input
session, not in general workflow preferences. Loading or changing the input
clears the segment list and returns to Whole track. A saved processing record
restores segments only when its input SHA matches; otherwise they remain
cleared. Segment processing does not change notes, syllables, lyrics, voicebank
settings or timing.

A clear horizontal scrollbar and **Move view left** and **Move view right** buttons remain visible below the editor view. The default piano-roll view uses 40 px note rows (notes paint at about 36 px), a 260 px/s time scale, and 11.5 pt lyric and piano-key labels. **Row height** changes only the display between 80%, 100%, 125% and 150%. **Readable** restores the default horizontal scale and centers the selection or current time; **Full project** and **Selection** remain available.

Choose **Time segments** in **Style & curves** to reveal a large, prominent **Start editing** button. Choosing it opens the full-screen editor directly; the page does not embed a separate piano-roll card. F11 can also open or exit the editor. The **Segment settings** panel on the right can be shown or hidden and starts collapsed on narrow screens. Exiting returns to the original page and leaves only the entry; the same controls, segment data, selection, and undo history are retained. The interface follows VWA's light or dark theme, while each style keeps the fixed identifying color described above. The view still edits only style-segment start and end times.

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
remain accepted with their historical recipes and defaults.

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
