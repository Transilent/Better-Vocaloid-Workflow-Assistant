# Reference expression: research and implementation

[English](SVExpression.en.md) | [简体中文](SVExpression.zh-CN.md)

Reviewed on 2026-10-16. This survey covers official documentation, tutorials written by voice tuners, reference-transfer projects and public singing corpora. It is a selected review of accessible primary sources, not an exhaustive crawl of the web. Video descriptions are identified separately from material whose methods could be read. The [source register](sv-expression-sources.json) records the distinction.

## Findings that affect the design

**An acoustic measurement is not an SV control value.** SV defines Breathiness as a balance of periodic and aspiration components, while Voicing reduces the periodic component from its normal maximum. A noise ratio cannot be copied directly into either knob. The current implementation keeps separate, bounded mappings. [SV 2 base parameters](https://sv2.docs.dreamtonics.com/en/parameters), [automation API](https://resource.dreamtonics.com/scripting/Automation.html).

**Work at phrase and articulation scales.** The following tutorials support editing overall expression before local gestures, paying attention to pronunciation and interactions between controls. They provide useful practices, not universal numerical presets.

| Primary tutorial | Relevant practice | Decision for BVWA |
| --- | --- | --- |
| [十竜: Tension](https://note.com/hiryukaoku/n/ndb59c5efb701) | Shape sections and phrases; consider the level change caused by timbre editing. | Preserve phrase continuity. A future rendered comparison should adjust Loudness after timbre. |
| [十竜: Breathiness](https://note.com/hiryukaoku/n/n81ad43416e35) | Breath can follow phrase starts/releases and interact with Tension. | Use measured evidence; do not impose inverse Tension or a sigh on every note. |
| [十竜: Voicing](https://note.com/hiryukaoku/n/nc0254f680803) | Small local reductions can soften an entrance or release. | Keep the normal neutral at 1 and avoid continuous, unsupported reduction. |
| [十竜: Vowels](https://note.com/hiryukaoku/n/nf7abd60519af) | Vowel, pitch and voice-database differences affect apparent strength and parameter response. | Condition harmonic-balance transfer on repeated vowel context; do not interpret all spectral changes as Tension. |
| [Pigeon's Nest: Studio tutorial](https://vocapigeon.wixsite.com/pigeons-nest/synthesizerv-studio-tutorial) | Demonstrates phoneme editing and combined voice controls. | Keep parameter selection independent and make generated curves editable. |
| [Zzz-T: introductory series](https://www.bilibili.com/video/BV134411m7fn/) | The description lists pitch, four main controls, note properties and splitting notes. | Chinese learning reference. Only its description/chapter list was inspected; no claims here rely on watching the demonstrations. Published in 2019, before current SV versions. |

**SV versions and voice databases matter.** Official migration guidance recommends rebalancing Vocal Modes and base parameters for version 2 voices and using phoneme timing rather than extra consonant-only notes. Consequently, this iteration does not insert guessed phoneme-duration attributes, Vocal Modes, Gender or Tone Shift. [SV migration guide](https://sv1.docs.dreamtonics.com/en/synthv/upgrade/migration-guide), [note properties](https://sv1.docs.dreamtonics.com/en/synthv/advanced-usage/note-properties).

## Projects worth learning from

| Project | Verified scope and limitation | Use here |
| --- | --- | --- |
| [Hataori Real Voice](https://github.com/hataori-p/real-voice) | MIT scripts for reference pitch and timing transfer, with a Praat workflow. The README flags path limitations and obsolete scripts. | Articulation and timing are part of expression; this is not a ready-made four-control predictor. No scripts copied. |
| [Hataori SynthV Voice Tools](https://github.com/hataori-p/synthv-voice-tools) | MIT, explicitly archived/experimental. MFCC matching and parameter-sequence search depend on per-voice samples; those banks are absent, and legacy alignment needs replacement. | Strong reason to build a measured target-voice calibration instead of increasing arbitrary gains. Not installed as a working dependency. |
| [Real Voice 2](https://github.com/Aeterrno-Amora/real-voice-2) | Reference-transfer prototype. Its README describes iterative dynamics estimation as future work. | Useful workflow reference; not evidence that automatic iterative SV timbre matching already works. No code bundled. |
| [OpenVPI DiffSinger](https://github.com/openvpi/DiffSinger/blob/main/docs/BestPractices.md) | Defines acoustic variance features and discusses coupling. Its Voicing is harmonic RMS in dB, which differs from SV's control. | Harmonic/noise analysis informs features; values and parameter restrictions cannot be transplanted to SV. No DiffSinger model bundled. |
| [AIST VocaListener](https://staff.aist.go.jp/t.nakano/VocaListener/) | Iterative synthesis-and-comparison estimates pitch and dynamics for a target synthesizer/database. | Basis for a future render-feedback loop, not a component already integrated into BVWA. |
| [voice2mode](https://arxiv.org/html/2602.13928v1) | Studies phonation classification with speech-model embeddings on sustained vowels from one singer. | Candidate for a later technique detector. Reported classification performance does not measure SV control prediction or generalization to separated songs. No new encoder installed. |

## Data that can actually help

All entries below lack calibrated SV knob labels. A technique label such as `breathy=1` is not equivalent to `breathiness=1` in an SV project. Dataset terms remain separate from BVWA's MIT software license.

| Corpus | Available supervision / useful role | Source terms and size information |
| --- | --- | --- |
| [GTSinger](https://github.com/AaronZ345/GTSinger), [official data](https://huggingface.co/datasets/GTSinger/GTSinger) | Multilingual controlled technique comparisons, including Chinese/Japanese; phoneme times, score and technique annotations. Useful for testing technique cues and separating articulation from expression. | 80.59 hours of singing. [CC BY-NC-SA 4.0 plus the publisher's additional terms](https://github.com/AaronZ345/GTSinger/blob/main/dataset_license.md). Four small annotations and their audio clips were inspected privately; no corpus is bundled. |
| [VocalSet 1.2](https://zenodo.org/records/1442513) | Controlled vowels, singers and techniques; useful for checking vowel/pitch confounds and technique recognition. | CC BY 4.0 in [registered DOI metadata](https://api.datacite.org/dois/10.5281/zenodo.1442513). The record lists a 6.0 GB reorganized archive and a 2.1 GB earlier archive; these are not two independent training corpora. |
| [Annotated VocalSet](https://zenodo.org/records/7061507), [paper](https://www.mdpi.com/2076-3417/12/18/9257) | F0, note boundaries, transitions, MIDI pitch and lyrics for VocalSet; useful for pitch/note evaluation. | 411.5 MB annotation archive. CC BY 4.0 in [registered DOI metadata](https://api.datacite.org/dois/10.5281/zenodo.7061507); original audio is a separate input. |
| [M4Singer](https://github.com/M4Singer/M4Singer) | Mandarin singing and musical scores; candidate for alignment/note evaluation. | [CC BY-NC-SA 4.0 and additional agreement](https://github.com/M4Singer/M4Singer/blob/master/dataset_license.md). Not acquired or used for training in this iteration. |
| [Opencpop](https://wenet-e2e.github.io/opencpop/) | Mandarin recordings with note/phoneme boundaries and pitch types; useful for checking merged or missing syllables. | About 5.2 hours; [CC BY-NC-ND 4.0](https://wenet-e2e.github.io/opencpop/liscense/). Not included in the app or used to fit a distributed model. |
| [PJS](https://sites.google.com/site/shinnosuketakamichi/research-topics/pjs_corpus) | Japanese speech/singing, MIDI, MusicXML and phoneme labels; useful for alignment and pronunciation tests, without phonation-mode labels. | Publisher lists 100 short songs, a 0.26 GB archive and CC BY-SA 4.0. Not downloaded. |

The inspected GTSinger JSON records have `ph`, `ph_start`, `ph_end`, note-time arrays and six technique arrays (`mix`, `falsetto`, `breathy`, `pharyngeal`, `glissando`, `vibrato`). They contain neither an SV voice-database identity nor numerical SV control curves. The four annotations totaled 49,644 bytes; four private research audio samples totaled 5,160,272 bytes. No dataset or learned weights are added to the application.

In a descriptive check of these two technique/control pairs, the current analyzer's median high-band noise was higher in the breathy take by 2.9234 dB (Chinese) and 1.6720 dB (Japanese). Median whole-band periodic fraction did not decrease. These are measurements from frames passing the existing voiced/pitch guards, not a classifier evaluation. They reinforce keeping Breathiness and Voicing separate; two pairs cannot establish generalization or calibrate a knob. Samples were selected by folder order before analysis, not by the desired result.

An [SVP crowdsourcing call](https://forum.dreamtonics.com/t/crowdsourcing-an-open-source-svp-dataset/761) requests projects and renders. That call alone is not a released, licensed paired dataset. This survey did not verify a downloadable corpus with aligned SVP controls, rendered audio, editor version, voice-database version and reuse permission. Individual cover projects may be helpful examples but cannot be assumed to satisfy those requirements.

## Implemented in timbre mapper version 3

These are BVWA engineering choices motivated by the review, not algorithms claimed by the tutorial authors:

1. **Validate each gesture locally.** Three consecutive frames of evidence are required at each location. One real event elsewhere in a song no longer validates unrelated one/two-frame spikes. Contributions decay over at most 40 ms of valid signal; gaps remain neutral. Reports include the gesture count and supported duration.
2. **Reduce a bounded part of vowel coloration in Tension.** The existing export dictionaries identify a single syllable nucleus without changing project lyrics or pronunciation. For each pitch neighborhood, at least two vowel groups must each have three independent syllables in two phrases. Each syllable contributes one body median; held-note fragments do not become extra evidence. Up to 60% of a ±3 dB group offset is removed. This partial correction retains differences between performances of the same vowel. Unknown/multiple syllables and insufficient comparisons retain the original pitch-conditioned mapping.
3. **Retain existing control limits and performance handling.** The four optional controls, 60% starting strength, short-note smoothing, measured releases, tempo mapping and reference-feature cache remain available. There are no new models or dependencies. This change does not modify notes, lyrics, pitch, Loudness or accompaniment in the controlled audition files.

The vowel context is derived from score text, not a newly measured phoneme alignment. It cannot identify every consonant boundary or fully undo vocal-tract filtering. A vowel correlated with one expressive section is specifically insufficient evidence for the correction. The coefficients are conservative defaults pending listening tests, not voice-database calibration.

### Follow-up: complete auditions and stable pitch context

Single-control exports leave the other controls disabled. Complete combined auditions are checked for real variation in all four curves on each vocal track. The application continues to respect independent control selection.

Mapper version 4 additionally uses written note pitches for spectral-reference neighborhoods. Tests reproduce a false Tension oscillation when small measured vibrato crosses a neighborhood boundary; the score context removes that reference switching while retaining a genuine harmonic-balance gesture. It keeps measured f0 for analysis and screening. Export receipts now distinguish disabled, skipped and applied controls, with serialized point counts/ranges; invalid or flat output marked as applied is rejected before saving. These changes do not establish a perceptual quality score or calibrated voice-database response.

### Follow-up: control continuity and local coupling

Mapper version 5 addresses two reproducible processing artifacts. An internal one/two-frame detection gap previously reset each control and added a new taper at both edges. Now a 20–40 ms control gap can be interpolated when both ends have independent, sustained, same-direction evidence, their reference pitches differ by no more than four semitones, and the score covers the whole interval without a rest. Gap measurements must be finite and above the existing energy gate. Interpolation changes only automation; original reliable/evidence durations stay unchanged. Quiet, longer, one-sided and opposing gaps remain excluded.

A separate regression reproduced Voicing attenuation from a rejected two-frame Breathiness impulse. Coupling now uses eligible, supported control profiles after their boundary taper. A skipped Breathiness curve, or one supported only elsewhere, no longer reduces Voicing at that location. Eligible overlapping controls retain the 35% maximum reduction. Tests cover independent selection, selection order, lower strengths and actual SVP serialization.

The feature cache now fingerprints the extractor and component loader separately from the mapper. This allows control mapping to evolve without repeating unchanged acoustic analysis. Full project export still invalidates when mapping code changes. Both changes use existing dependencies and add no model weights. These thresholds are engineering defaults for bounded continuity, not learned phoneme timings or voice-database calibration.

Version 6 extends this phrase-level handling to a different case: a measured, still-voiced control briefly dipping below its evidence threshold. It retains the measured contribution for at most three 20 ms frames between independently validated cores when both raw and smoothed values retain the same direction and exceed half the threshold. It does not seed new gestures from weak evidence, interpolate acoustic features, span unvoiced regions or hold through a sign reversal. This reduces threshold chatter without adding a generic curve to every note. Reports distinguish held measured evidence from interpolated detection gaps; tests cover each exclusion and rejected isolated impulses.

## Next calibration step

The missing link is a target voice's measured response. A practical experiment would:

1. Record editor/voice version, Vocal Modes and voice settings. Render a small, fixed score over several vowels, registers and safe parameter levels, keeping notes, pitch and effects fixed.
2. Compare the same vowel/register at baseline and changed controls. Fit monotonic local responses only where they are supported; measure cross-effects on level and the other acoustic features.
3. Apply the inverse response conservatively, then compare a rendered draft with the reference. Correct relative level after timbre; avoid interpreting vocal-tract or singer identity differences as an error to erase.
4. Validate on held-out phrases and separate singers/songs for any trained technique detector. Include short notes, connected phrases, releases and separation leakage. Report classification metrics separately from parameter error and listening preference.

No model was trained and no SV audio render was performed for this review. The current changes are covered by deterministic signal tests and offscreen project readback; perceived improvement needs audition in the selected voice. See [reference expression usage](../ReferenceDynamics.en.md).
