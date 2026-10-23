# Optional video assembly and separation paths

[English](OptionalWorkflows.en.md) | [简体中文](OptionalWorkflows.zh-CN.md)

These features are part of the development version. They have not been published as a new release.

## Video assembly

**视频合成** is a separate sidebar page. It accepts an original video, a Mixdown and an output filename without requiring a music-processing task. The publishing workflow accepts finished videos directly, so assembly is optional.

Automatic alignment estimates a shared energy/rhythm pattern and rejects ambiguous matches. Use manual offset when the recordings lack sufficient common material. Positive offsets delay the Mixdown; negative offsets advance it. Optional two-pass normalization targets −14 LUFS. The original video packets are copied and its original audio is replaced. Audio is padded or trimmed to retain the full video duration.

Inputs and existing output files are not overwritten. Cancellation removes temporary output. After checking the result, use **将成品带入发布工作流** to fill in the video field; this does not start an upload. Active assembly blocks update installation and cache cleanup. Its working files belong to the removable `cache/video-mixdown` cache.

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

## Validation limits

Developer tests cover optional-page navigation, manual handoff to publishing, cancellation, busy-state interlocks, real FFmpeg alignment/mux/normalization, independent-input routing, residual consistency and calibration safety. Actual Chinese and Japanese original-mix excerpts have also run through HQ3 on DirectML and both BS paths on CUDA. These are short functional checks, not a full-song quality benchmark. SV-rendered calibrated comparisons and listening evaluation are still required.
