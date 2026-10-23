# Experimental singing style presets

[English](StylePresets.en.md) | [简体中文](StylePresets.zh-CN.md)

The independent **Tuning workflow** accepts an existing task or imported SVP
and can produce a separate style candidate. The three initial choices are
**Soft ballad**, **Intimate narration** and **Power pop**. The default retains
the original performance. Choose the strength and vocal tracks on the tuning
page; preparation's project-export switches do not gate these settings. See
[Tuning workflow](TuningWorkflow.en.md) for source snapshots and recovery.

The presets are manually authored experiments for audition. They are not
learned corpus coefficients, a semantic classifier, a calibrated voicebank
response or a promise of a musical genre. They use Loudness, Tension,
Breathiness and, where applicable, Voicing. Optional **Add pitch expression**
adds local gestures to the existing `pitchDelta`. It defaults to off, including
older tasks without the option. Bank-specific Vocal Modes are not written;
each bank and editor version needs its own verification.

|Preset|Intended draft|
|---|---|
|Soft ballad|Softer entrances and releases, a modest phrase core, restrained tension and local breath at the end.|
|Intimate narration|A quieter, less tense phrase with a voiced core and more breath toward the edges.|
|Power pop|A stronger entrance and sustained core, less breath and a controlled ending. No scream or growl synthesis.|

Phrase windows follow note timing and gaps, not lyric meaning or automatic
emotion recognition. A short gap inside a phrase belongs to that selected
window; longer rests remain unchanged. Notes, lyrics, timing,
voice settings, retakes, accompaniment, unknown controls and unselected tracks
are preserved. Original control-point positions remain. Selected parameter
values can deliberately change by recorded additive envelopes; this is distinct
from shape-preserving curve densification. Existing long-note variation is
retained. A shared headroom scale avoids clipping individual peaks flat.
Unsupported populated nonlinear curves and out-of-nominal source values stay
unchanged, with explicit partial/skipped reasons. If no channel can change,
export stops clearly and retains the baseline. The first version supports flat,
untrimmed main groups; linked library-group selections are rejected.

Optional pitch gestures distinguish phrase entry, connected intervals and
phrase release. Ballad entries are slower, narration entries shorter, and pop
entries more emphatic. Connected-note offsets follow the interval direction.
Release gestures dip locally and rejoin the source. Short, overlapping and
non-singing notes are left without new ornaments. Narration adds no vibrato.
The other two presets allow subtle delayed vibrato only on long, locally flat
pitch with explicitly disabled original automatic vibrato. Existing or unknown
vibrato states are retained. Note attributes and `vibratoEnv` never change.

Original pitch-knot positions remain, while selected values may deliberately
change by recorded additive gestures. The source function outside those windows
remains unchanged. Populated nonlinear pitch curves are skipped intact.
Out-of-nominal source values are preserved; only affected gestures are omitted,
with partial application reported. Units and interpolation definitions follow
[Dreamtonics Automation](https://resource.dreamtonics.com/scripting/Automation.html).
These definitions do not supply the application's experimental style recipes.

The output folder contains `voices.svp` and an independent
`voices-style-<preset>-<signature>.svp` in compatible legacy task exports.
Independent tuning sessions instead save `outputs/tuned-<signature>.svp` and
an adjacent `.svp.tuning.json` receipt. A style change generates another
candidate; old candidates remain on disk but their producer receipts become
inactive. Retry starts from the verified baseline, never from the last styled
candidate. Modified files are preserved before producer recovery. The report's
`styles` section and export receipts bind the source hash, settings, applied
channels, partial reasons, headroom and density result. Style settings do not
invalidate ASR or MIDI inference.

For an existing SVP, run the generated runtime module from the application root:

```powershell
dependencies\vocal2midi\python\python.exe -B style_presets.py A.svp B.svp --preset soft_ballad --strength 0.6 --tracks 0 --density dense
```

Append `--style-pitch` to include pitch gestures. The same UI option persists
with workflow preferences. Candidate cache signatures include the option and
pitch implementation, without invalidating MIDI inference or baseline export.

The standalone command refuses to overwrite the source or a different existing
candidate. It writes a `.svp.style.json` receipt and uses that adjacent receipt
to refuse a known candidate as its next input. Keep the receipt when copying a
candidate: the project alone cannot reliably identify its provenance. Always
start from the original performance. The application pipeline selects its
verified baseline independently. Synthesis and listening remain separate user
actions.

Methods informing the directions include [slow-song articulation](https://note.com/city_music55/n/nf53d5476edf1),
[local breath/voice technique](https://note.com/hiryukaoku/n/nedd8dbd1acc8), and
[a rock-chorus case](https://note.com/in_densetsu/n/n9e434e7d41af).
The numerical recipes are this application's audition drafts, not values
copied from these sources or validated for another bank.

Option and numeric inputs ignore wheel changes, even while focused. The wheel
still scrolls pages and browses combo lists without committing a selection;
mouse clicks and keyboard selection remain available.
