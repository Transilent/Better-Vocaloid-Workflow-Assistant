# Experimental singing styles

[English](StylePresets.en.md) | [简体中文](StylePresets.zh-CN.md)

The independent **Tuning workflow** accepts an existing task or imported SVP
and offers **Gentle [Experimental]** or **Bright & Powerful [Experimental]**.
The default preserves the original performance. Whole-track mode uses one style;
non-overlapping time segments may each choose either style. Neither mode stacks
on an earlier style candidate. Settings apply only when the user regenerates a
candidate. Opening, restoring or registering an older project does not rewrite
it. See [Tuning workflow](TuningWorkflow.en.md) for source snapshots and recovery.

The user chooses the style manually; the application does not analyze lyrics or
infer emotion. The style overlay follows the primary expression source, and every
attempt starts from the verified original-performance snapshot. Switching styles
or retrying never stacks on an earlier styled candidate.

| Style | ID | Public description |
|---|---|---|
| Gentle [Experimental] | `gentle` | Soft entrances, a stable voiced core, and a tapered release; each phrase segment can be adjusted separately. |
| Bright & Powerful [Experimental] | `bold` | Quick entrances, crisp connections, and a solid sustained core; each phrase segment can be adjusted separately. |

These descriptions identify draft directions awaiting audition. They are not
measured voicebank results, listening guarantees or physical voice categories.
The recipe values are manually authored, not learned corpus coefficients,
automatic semantic classifiers or calibrated responses from a target voicebank.
Brightness and distortion risk vary with the voicebank, version and settings.
The user must synthesize and audition the result in the selected voicebank.

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

- **Whole track** applies the current visible style card once to the selected
  actual target tracks.
- **Time segments** show notes from those tracks in a simple piano roll with
  horizontal time selection. Drag to select, press Esc to clear, and use Ctrl
  plus the wheel or the zoom buttons to zoom. This view is for notes and style
  ranges only; it is not a full MIDI editor and does not edit pitch, syllables,
  lyrics, timing or the voicebank.

In Time segments mode, select a valid time range, set the style, overall
strength, four segment coefficients and added-pitch choice, then choose **Add
segment**. Selecting a list row loads that segment's settings back into the
controls. **Update selected segment**, **Delete selected segment** and **Clear
segments** manage the current input. Segments cannot overlap, at most 128 are
allowed, and a selection shorter than 25 ms cannot become a segment.

When the segment list is nonempty, execution reads only that list. Each segment
can use its own `gentle`/`bold`, overall strength, four coefficients and pitch
switch within its start/end time. Outside a segment, the style line produced by
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

## Pitch defaults and compatibility mapping

The `gentle` and `bold` IDs enable added local pitch by default; an explicit
off choice is retained. Opening an older project or candidate does not
automatically rewrite the stored style settings or candidate file. A restored
legacy ID is displayed with its merged card. Saved overall strength and an
explicit `style_pitch=False` are retained; when `style_pitch` is absent, the
original legacy ID's default applies. When the user later saves preferences or
regenerates a candidate, the new selection and result use `gentle` or `bold`.

| Legacy ID | Displayed style |
|---|---|
| `soft_ballad`, `intimate_narration`, `melancholy`, `fragile_tender` | `gentle` |
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
line remains. `gentle` and `bold` include added local pitch by default; use
`--no-style-pitch` to stop pitch while retaining timbre segments. Legacy IDs
remain accepted with their historical defaults.

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
