# Preparation and tuning workflows

[English](TuningWorkflow.en.md) | [简体中文](TuningWorkflow.zh-CN.md)

The main sequence is **Preparation → Tuning → Video assembly → Publishing**.
Video assembly remains optional; a finished video can go directly to publishing.
Voicebank synthesis and final publication remain explicit user actions.

Preparation accepts online or local music, separation results, lyrics, MIDI and
editor-project export options. It produces the score and source material. Its
saved tuning preferences do not implicitly process the newly prepared project.
When regenerating compatible notes, retaining already authored automation stays
available as preservation rather than a new acoustic fit.

Tuning works independently of the preparation page's MIDI/export toggles. Select
an existing task's SVP or import an SVP, select the vocal tracks and explicit
reference mappings, then generate a separate candidate. The initial editing
format is SVP. Other formats must be converted and checked separately; this
workflow does not silently discard unknown parameters through conversion.

## One expression source

Choose one primary source:

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
unselected tracks and the score remain protected. Existing nonlinear curves may
be kept with a partial/unsupported receipt rather than rewritten inaccurately.
Flat, untrimmed mainGroup selections are supported; linked group arrangements
need a separately prepared editor copy.

## Recovery and provenance

A session snapshots the source bytes in `inputs/source.svp`. Every attempt starts
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
