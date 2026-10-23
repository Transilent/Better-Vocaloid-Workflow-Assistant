# Learning from reference projects

[English](ReferenceLearning.en.md) | [简体中文](ReferenceLearning.zh-CN.md)

The research tools compare parameter-bearing SVP projects with local reference vocals. They produce an inventory and offline evaluation reports. They do not install a learned profile, replace recognition weights or modify the reference projects.

## Project audit

Run from a prepared source checkout with the bundled numerical runtime:

```powershell
dependencies\vocal2midi\python\python.exe -B tools\research\reference_corpus.py "path-to-reference-collection" work\reference-audit
```

The collection contains `svp_100/*.svp` and a UTF-8 `svp_bilibili_map.csv`. The mapping uses the columns `#`, `曲名/项目`, `投稿者`, `BowlRoll链接` and `B站链接`. Numbered project filenames identify mapping rows.

The audit examines audible, referenced note groups. Unused library snapshots and repeated copies do not become independent examples. Zero-filled automation, fixed offsets and uniform converter defaults are excluded as evidence of a changing performance. Varying automation and nonuniform musical note or phoneme edits qualify for further inspection. This classification detects expression data; it does not prove that every edit was made manually or sounds good.

Malformed curves are reported and excluded individually. Other valid expression in the same project remains available. Input hashes are checked after analysis. The output records exclusion reasons, control availability, voice names, curve modes and source hashes.

Alternate tuning versions are grouped by video, normalized song title and score fingerprints before training/holdout assignment. The score fingerprint ignores UUIDs, tuning, constant transposition and a constant time offset. This reduces duplicate leakage; it cannot identify every arrangement or translation of a song.

## What the labels mean

An SVP parameter is a control for its assigned voice, rather than a direct measurement of a singer. The same tension setting can behave differently across voicebanks and synthesis versions. A reference video may contain the original singer, another cover, effects or a shorter arrangement. A matching song title alone does not establish a training pair.

Only exact source control points inside audible notes are used as labels. Native cubic interpolation is not replaced with guessed linear or PCHIP values when constructing teacher labels. The editor's available interpolation modes are described in the [official Automation API](https://resource.dreamtonics.com/scripting/Automation.html).

The score-only trial predicts relative contour shapes from note duration, phrase position and melodic context. Labels are median-centered within their group and scaled for this benchmark. Each project receives equal weight, with groups sharing that weight. Results are evaluated on held-out projects from different song clusters. A positive score-only result would still not establish an audio-to-voicebank mapping.

## Paired-audio trial

First prepare a local vocal reference and its verified RMVPE pitch sidecar. Align the score with the vocal, checking the complete selected track, constant offset, transposition, coverage and pitch agreement. Short or transposed harmony fragments must not displace a substantially more complete melody match. Select a track that actually contains the control being studied.

The acoustic tool reads a JSON manifest containing `id`, `audio`, `pitch_cache` and `selection` for each example. `selection` contains `track_index`, `offset_seconds`, `transpose_semitones`, `score_voiced_coverage`, `pitch_agreement` and `compared_seconds`. Media paths resolve relative to the manifest. Selection is a recorded alignment result, not a user-adjustable quality score.

```powershell
dependencies\vocal2midi\python\python.exe -B tools\research\reference_audio.py work\reference-audit\corpus.json work\reference-audio-manifest.json work\reference-acoustic
```

The trial uses the installed WORLD component and existing numerical libraries. Spectral tilt, aperiodic energy, periodicity, level and pitch are centered using the unlabeled reference audio. Exact tension knots additionally require local WORLD and RMVPE pitch support. Per-note sampling limits prevent dense curves from dominating.

The target is the control's variation around its group median. Absolute voicebank offsets are outside this trial. Median subtraction defines the evaluation target; it does not provide a deployable estimate of the missing bank offset. The primary model is a fixed, regularized linear fit. A secondary pseudo-Huber fit tests resistance to unusual control points, using the same features and song partitions. Its fixed 0.02 control-unit smoothing and regularization are not tuned against held-out songs. Validation leaves out an entire song, including all its supplied alternate projects. It never splits adjacent frames of one recording between training and validation. Comparing these candidates on one small cohort is exploratory, rather than a new independent final test.

The report separates globally verified pairs from a broader exploratory set of locally supported samples. Local agreement does not turn a failed global pair into a verified one. The current conservative mapping is included as a diagnostic comparison at strength 1.0, without voicebank calibration. Error against a human control curve measures curve agreement, not listening quality or synthesized-audio fidelity.

## Applying results

Both tools write `runtime_enabled: false`. Research coefficients are not loaded by the assistant. A candidate needs more independently verified pairs, evaluation for the target voicebank and controlled editor re-renders before it can replace an existing mapping. Failed candidates remain documented instead of being applied merely because a fit completed.

Reference projects, downloaded recordings and private file paths stay outside the source distribution. Retain attribution and source records locally; a project's download availability does not establish permission to redistribute its contents or derived model weights. The application adds no recognition or separation weights for this research workflow.
