# Optional video assembly and separation paths

[English](OptionalWorkflows.en.md) | [简体中文](OptionalWorkflows.zh-CN.md)

## Consolidated experimental singing styles

The Tuning workflow now lists only **Gentle [Experimental]** and **Bright &
Powerful [Experimental]**, with the original performance preserved by default.
Whole-track mode uses one style; different non-overlapping time segments may each
choose either. Neither stacks on an earlier style candidate. Overall strength
and four `0…1` segment coefficients, `onset`, `sustain`, `connection` and
`release`, scale local changes: onset and release affect timbre and added pitch,
sustain affects timbre only, and connection affects local pitch links only.
Disabling added local pitch stops pitch gestures. Notes shorter than 160 ms,
rap/BR material, silence and overlapping notes receive no new gestures. Existing
nonlinear curves, vibrato states and unknown content remain protected.

Time segments show notes from the selected actual target tracks on a simple
piano-roll view. Drag horizontally to select time, then keep a style, overall
strength, four segment coefficients and pitch choice independently for each
non-overlapping segment. When the list is nonempty, only segment ranges receive
style changes; outside them, the primary source's style line remains. Reference,
calibration or retained primary processing still covers the selected whole
tracks. Up to 128 segments are allowed, and selections shorter than 25 ms are
invalid. The piano roll displays notes and selects ranges; it does not edit
notes, syllables, lyrics, timing or the voicebank.

`style_regions` belongs only to execution options for the current input session,
not general preferences. Changing the input clears it, and record recovery
requires a matching input SHA. Legacy IDs remain readable from existing tasks and
older command-line records. Opening an older project or candidate does not
automatically rewrite the file; the UI displays its merged `gentle` or `bold`
card, and a later preference save or candidate regeneration uses the new ID.
Historical pitch defaults still follow the original ID. See [experimental
singing styles](StylePresets.en.md) for the public choices, segment record, and
audition limits.

## Video assembly

**视频合成** is a separate sidebar page. It accepts an original video, a Mixdown and an output filename without requiring a music-processing task. The publishing workflow accepts finished videos directly, so assembly is optional.

Automatic alignment estimates a shared energy/rhythm pattern and rejects ambiguous matches. Use manual offset when the recordings lack sufficient common material. Positive offsets delay the Mixdown; negative offsets advance it.

Before rendering, click **Analyze audio alignment** in **Pre-render audio alignment**.
The assistant compares the source video's first audio track with the Mixdown
using shared energy/rhythm changes and reports a suggested offset and whether
the evidence is reliable. For a reliable result, **Use suggested offset**
copies it to the manual offset. With automatic alignment enabled, a verified
pre-render result can also be reused during assembly. Weak, ambiguous or stale
analyses are not adopted automatically; audition the Mixdown and enter a manual
offset instead. Manual offsets remain available at any time. Optional two-pass normalization targets −14 LUFS. The original video packets are copied and its original audio is replaced. Audio is padded or trimmed to retain the full video duration.

Inputs and existing output files are not overwritten. Cancellation removes temporary output. After checking the result, use **将成品带入发布工作流** to fill in the video field; this does not start an upload. Active assembly blocks update installation and cache cleanup. Its working files belong to the removable `cache/video-mixdown` cache.
Analysis files use `cache/video-alignment`. Analysis compares energy changes
only; it neither classifies musical content nor replaces listening.

## Separation without cascaded lead filtering

The built-in Karaoke 2 route remains total vocals → lead/backing. With the optional BS-RoFormer component, two paths are available:

| Path | Model inputs | Final stems |
| --- | --- | --- |
| Default cascade | Original mix → HQ3 total vocals; total vocals → karaoke model | Lead, residual backing and HQ3 instrumental |
| Independent lead, experimental | HQ3 and BS karaoke each receive the original mix | BS lead, HQ3 total vocals minus BS lead, HQ3 instrumental |

Both paths make **two model invocations**. The experimental path reduces the maximum model depth of the lead from two to one. It is not a single model that predicts three semantic parts. No model weights are changed, and Chinese/Japanese use the same separation path. Their ASR selection remains separate.

Independent subtraction preserves sample timing and reconstructs the original mix, but disagreements between the models can leak into the backing stem. A near-perfect stem sum only establishes arithmetic consistency; it does not establish separation quality. Listen to both lead and backing before choosing this route. The default remains unchanged.

The checked [BS karaoke checkpoint](https://huggingface.co/becruily/bs-roformer-karaoke) declares two outputs, Vocals/Instrumental. The [Karaoke 2 model card](https://huggingface.co/musetric/uvr-mdxnet-kara2-onnx) specifies isolated vocal input, so that model is not offered for original-mix lead extraction. No verified drop-in checkpoint with explicit Lead/Backing/Instrumental outputs has been integrated. A future three-output adapter should validate stem meaning, normalization, channel layout and exact sample counts before it replaces these routes.

## Automatic checks for shared lead audio

After automatic lead/backing separation, the assistant checks for highly coherent, in-phase copies of the lead that remain in the backing. It moves only the shared frequency components into the lead. This uses the installed signal-processing libraries and adds no model weights or neural separation stage. Imported stems are left as supplied.

The check preserves sample count, stereo channels and the summed vocal signal. It does not swap stems based on volume, promote backing-only passages or normalize their levels. Processing uses short overlapping blocks on a consistent analysis grid. `separation-report.json` records the method and affected intervals; the transfer-energy figure is a signal measurement, not an accuracy score.

This is a conservative correction, not a guarantee of clean stems. Delayed or spectrally different leakage can remain. Intentionally phase-locked unison doubling can be acoustically indistinguishable from a leaked copy. Existing tasks are not rewritten merely by opening them; reprocessing creates or explicitly reruns a task.

## Measured voicebank fitting

The calibration experiment is described in [Reference expression](ReferenceDynamics.en.md#measured-voicebank-response-fitting-development-experiment). It prepares one seven-track trial project, waits for a single user-triggered track export in SV, and automatically collects and validates the seven WAVs before fitting a separate project. It adds no neural model weights. Calibration belongs to the current score, is separate from the default reference-to-control heuristics and is disabled by default. SV's Bounce action remains manual.
