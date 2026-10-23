# Reference expression (experimental)

[English](ReferenceDynamics.en.md) | [简体中文](ReferenceDynamics.zh-CN.md)

Reference expression adds editable Loudness, Tension, Breathiness and Voicing curves to a Synthesizer V project. Each control is optional. It follows relative acoustic changes in the separated vocal to prepare an initial tuning draft. Final results depend on the selected voice database and should be auditioned there.

This feature is available in the development build. It is disabled by default.

## Use

1. Open the independent **调音工作流** (Tuning workflow). Choose an existing preparation task's SVP or import an SVP.
2. Select the vocal tracks and explicitly map each track to its matching reference vocal.
3. Select reference expression as the primary source and enable Loudness as needed. For other controls, first install **WORLD** under **模型与组件 → 参考演唱音色**, then select Tension, Breathiness and/or Voicing.
4. Choose the strength and curve density, then generate an independent candidate. Inspect and audition its curves in the selected voicebank. Preparation's MIDI/project export switches no longer gate these controls.

Tuning sessions retain their input snapshots and options, so reruns do not stack the previous candidate. Reference mapping and measured calibration are exclusive primary sources; style overlays follow the selected source before one final density pass. See [Tuning workflow](TuningWorkflow.en.md). Existing preparation tasks without the new preparation-only flag continue using their recorded options.

Pitch export remains independent. Disabling pitch does not disable reference dynamics or insert replacement pitch curves. MIDI, USTX, VSQX and separated audio are not given this SV-specific automation.

## How it works

The analyzer measures centered, DC-removed energy in short windows, averages channel powers without cancelling opposite-polarity stereo, and uses aligned note regions to choose a reference baseline. It smooths the relative envelope, limits the requested control range and stores a sparse linear curve. At 100% the correction is bounded to −12 to +6 dB; the strength scales this range. This is an additional relative control, not a calibrated prediction of the selected voice's rendered level.

Long gaps reset to zero. Releases follow the measured source; a fixed fade is not added to every note. Silent parts, insufficient valid signal and nearly constant levels do not create placeholder curves. Analysis statistics and timestamps are recorded in `exports/report.json`, under the SVP output's `reference_dynamics` field. Signal-validity statistics are energy-based heuristics, not a singing-technique recognition score.

Loudness reuses existing audio libraries. The other controls use an isolated [PyWORLD 0.3.5](https://pypi.org/project/pyworld/0.3.5/) component: a 218 KiB download and approximately 447 KiB installed, with no model weights. The installer verifies its pinned SHA-256 and native runtime before activation. It does not update the shared Python environment.

## Timbre controls

The analyzer processes short blocks on the CPU. WORLD estimates periodic and aperiodic spectral components; the existing score constrains which detected pitches are plausible. Unvoiced, isolated, very quiet or pitch-inconsistent frames are excluded. Spectral tilt is centered within pitch neighborhoods to reduce melody-dependent coloring. Tension follows relative harmonic balance; Breathiness follows relative high-band noise; Voicing only makes small reductions where periodicity decreases. These are acoustic heuristics, not calibrated inverse controls or labels for singing techniques. Separation leakage, vowels and effects can influence them.

At 100% strength, Tension changes are bounded to ±0.35, Breathiness to ±0.30 and Voicing to 0.82–1. At 60%, the corresponding limits are ±0.21, ±0.18 and 0.892–1. Selecting Breathiness and Voicing together reduces Voicing only where both changes overlap, by up to 35%. Other Voicing sections retain their contribution. Unsupported gaps return to their neutral controls: 0 for Tension/Breathiness and 1 for Voicing.

Notes lasting 350 ms or less use a shorter smoothing window to retain brief gestures. Longer notes keep slower smoothing; the curve is not reset at every note boundary. A gesture must have at least three consecutive frames of measured evidence before strength scaling. Voicing uses a separate evidence threshold, so a sustained small change is not discarded by a threshold designed for Tension. One- or two-frame impulses, almost constant signals and changes below the output resolution still do not produce placeholder curves. These are signal-processing rules, not a voice-database calibration. Reports under `reference_dynamics.tracks[].timbre` include evidence duration, short-note coverage and overlap reduction.

Mapper version 3 validates evidence separately at each gesture, so a genuine change elsewhere in the song cannot validate isolated noise. Tension also uses repeated syllable nuclei at comparable pitches to reduce part of the vowel-dependent spectral bias. It requires independent syllables across multiple phrases, counts a held syllable only once, and leaves insufficient or unknown context unchanged. This is a partial correction derived from score text, not measured phoneme timing. Reports include `gesture_count`, `gesture_seconds` and Tension's `vowel_conditioning` statistics. See the [research and dataset review](research/SVExpression.en.md) for sources, limitations and the proposed target-voice calibration.

Version 4 uses the written note pitch for the reference neighborhoods in normal exports. A measured vibrato crossing a neighborhood boundary no longer switches that note's spectral reference on every cycle. Measured f0 is still used for spectral analysis and signal screening; the exported pitch and note data are not changed. The mapper reports `pitch_context: score` for this path.

Version 5 keeps supported control changes connected through internal 20–40 ms detection gaps. Both ends must have sustained evidence in the same direction, remain within four semitones and belong to continuous score coverage. The gap must remain audible and have finite measurements. Only the control is interpolated; missing acoustic evidence is not invented or counted as reliable signal. Quiet sections, score rests, longer gaps and opposing gestures remain separate. Reports record this interpolation as `bridged_gaps` and `bridged_seconds`.

Breathiness can now reduce the overlapping Voicing contribution only when its own curve qualifies for export at the selected strength. The reduction uses its supported, tapered contribution. Skipped Breathiness, isolated impulses and changes elsewhere in the song do not affect a Voicing-only passage. Control selection order does not change the result.

Version 6 also avoids abrupt gating when a measured gesture briefly dips just below its evidence threshold. It retains the measured control through at most 60 ms between two independently validated, same-direction gesture cores, provided the signal stays voiced and above half the evidence threshold. A weak signal on its own cannot start a curve. Longer dips, near-neutral values and sign reversals keep the original gating. The report separates this measured continuity (`held_evidence_gaps`, `held_evidence_seconds`) from interpolated detection gaps.

Export logs list the enabled controls. Each track's `parameter_status` distinguishes disabled controls, skipped analysis and successfully written curves, including the actual point count and range. Before replacing the project, the exporter verifies the serialized curves for controls marked as applied: a missing, flat, non-finite or invalid curve fails validation. Quiet or constant references can still be skipped without adding placeholder curves. Existing settings for controls that were not regenerated remain untouched.

## Repeated exports and storage

The same task can reuse compressed WORLD features from `temp/reference-features` when exporting different strengths or control selections. There is one cache per voice. Audio, note timing/pitch, component version, feature-extraction code or component-loader changes invalidate it; damaged caches are recomputed. Mapping-only changes can reuse the measured features while regenerating project curves. Older cache formats are rebuilt on first use. Cache writes are atomic and cancellation preserves the previous cache. The normal task-temp cleanup can remove these files. No additional model weights are needed.

This cache applies to repeated export within a task. The interface's **用当前工作流重做** creates a separate task and retains its existing reprocessing behavior; it does not share this temporary cache across tasks.

The supported ranges come from the [Synthesizer V automation API](https://resource.dreamtonics.com/scripting/Automation.html). The analysis is informed by [WORLD](https://github.com/mmorise/World) and the harmonic/noise approach used in [DiffSinger](https://github.com/openvpi/DiffSinger/blob/main/utils/decomposed_waveform.py); DiffSinger code and model weights are not bundled. Vocal Modes, phoneme timing and automatic editor rendering remain outside this prototype. Gender and Tone Shift are available only in the measured-response experiment below.

## Measured voicebank response fitting (development experiment)

This optional path measures the selected voicebank on the **current score**. It jointly fits Loudness, Tension, Breathiness, Voicing, Gender and Tone Shift where their responses can be distinguished. It is a local acoustic approximation, not a voice clone or a calibration reusable across arbitrary songs. It is disabled by default.

1. Generate an SVP, assign the intended voicebank in Synthesizer V and save a separate copy. Keep notes, lyrics, tempo and pitch curves unchanged. The first implementation requires all selected-track notes in its main group.
2. Open the measured calibration action in **调音工作流**, select that copy, the matching reference vocal and the lead/backing/vocals assignment. Set the save folder and strength, then choose **准备试音并等待导出**. The assistant creates and opens one seven-track `calibration.svp`: a neutral track and six tracks changing one control each. The reference must start at project time zero; unrelated intros/outros are allowed outside the written vocal region.
3. In SV's Render panel, check that all seven trial tracks are selected and MixDown is off, then trigger **one track-by-track WAV export**. Keep the prefilled destination and names, or use the same custom directory selected in the assistant. Export from zero to the same end time, with the same voice/settings and without accompaniment, normalization, compression or reverb. Trial mixer gain/pan/effects are reset, and bypass options are set where supported; check external editor effects too.
4. Leave the assistant waiting. It recognizes this session's track-name tokens, waits for all seven files to stabilize and close, checks finite samples and consistent formats/lengths, and automatically collects the renders and fits `calibrated.svp`. No manual renaming or seven separate project exports are needed. The source project and reference audio are retained. Relative media references in the new file are resolved against the original project location.

**Stop** cancels waiting or fitting and retains the session. **继续已有校准…** can load its `session.json` after restarting the assistant. Existing calibrated projects are not overwritten. Changed source/reference files require a new session. The generated profile is enabled in the current workflow only if its score and source vocal match that task; calibrating an unrelated external project does not enable it for the next song.

After a fit, **重新拟合 · 复用试音** reuses the same seven renders at a different strength and saves a new, dated SVP with its own report. Changing strength does not require another SV export. Audio and score identities are checked again before fitting. Feature extraction is cached separately from fitting, so changes to the temporal rules can reuse unchanged measurements.

This is a **single-export assisted flow**. The assistant does not trigger SV's Bounce action. No documented WAV-export method was found in the reviewed [SV scripting interface](https://resource.dreamtonics.com/scripting/SV.html); the [official render guide](https://sv1.docs.dreamtonics.com/en/synthv/basic-usage/render) describes track-by-track export through the Render panel. Editor-side rendering and perceptual results still need validation on the user's SV version. The older Python `create_probe_set` function remains available for separate-project probes.

### Preparing and recovering in the assistant

The calibration dialog now includes **检查工程与歌词**. It checks the selected track, voice assignment, reference duration and continuous-pitch data before preparing trials. For matching Chinese scripts it lists differences between reference lyrics and note text with approximate timeline ranges. These are review candidates, not verified transcription errors; Japanese romaji is not compared literally with Kanji lyrics.

**从 SVP 读取声库设置** imports the assigned voice from the first configured vocal track in a saved SVP. It does not copy that song's notes, lyrics or parameter curves, and retains the current song's language. **仅重建连续音高** runs the bundled RMVPE on the selected reference without repeating ASR or separation. Both actions write a separate prepared project. Pitch rebuilding keeps note timing and lyrics, replaces explicit pitch data, and disables automatic note pitch transitions/vibrato so they are not layered over the measured contour. No new model download is required.

The seven-file table distinguishes missing files, files still being written, stable files and validated files. The render folder can be opened or copied from the dialog. Incorrect aspiration mode, truncated exports, duplicates and changed inputs show recovery actions; existing renders are retained when retrying. If the score changes, **重新准备试音** creates a new session rather than reusing incompatible renders.

A successful fit also creates a neutral comparison with the same notes, pitch, voice and media references. **从校准／任务文件夹继续…** resumes by selecting a folder; users do not need to locate or edit JSON. The dialog restores the most recent result, including dated refits, and provides buttons for both fitted and neutral projects. User-edited comparisons and earlier results are preserved.

### Choosing the aspiration mode

Set **SV 气声输出** in the assistant to match the option used for rendering. Ordinary mono/stereo is the default. For **As extra channels**, the file already contains the complete voice; the extra aspiration copy must not be added again. For **As extra channels (isolated)**, periodic and aspiration components are summed to reconstruct the complete voice. In the supported four-channel SV layout, channels are interleaved: main-left, aspiration-left, main-right, aspiration-right. The two-channel layout contains one main/aspiration pair. Interpretation is explicit; a four-channel WAV is never silently treated as ordinary stereo.

Analysis reads these components directly from the original WAV. It creates no converted audio copies. The selected mode is stored with the session, render receipt and calibration profile and participates in feature-cache validation. Existing renders can therefore be reused by selecting the correct mode and continuing the session. Channel count, unsupported sample rate and insufficient duration have separate errors; a four-channel error does not mean audio was truncated.

The interface explains the seven trials and displays fit strength as a percentage. This percentage scales parameter changes from the baseline; it is not a recognition or perceptual-accuracy score. Progress identifies the reference, neutral and six probe analyses before the joint fitting stage.

The automatic session writes `render-receipt.json` and `fit-report.json` beside its projects; normal workflow exports report through `exports/report.json`. The exporter rejects changes to score, tempo or pitch and an explicitly conflicting voicebank. All renders and WORLD must be available when reusing the profile.

The analyzer combines level, harmonic tilt, aperiodic energy, periodicity and eight spectral-envelope cepstra on a 20 ms grid. Each probe is measured against neutral. Weak or redundant responses are excluded; regularized, bounded least squares fits supported vocal frames. Accepted changes are smoothed and unsupported regions return to neutral. Applied tracks replace the six control curves and their offsets using the measured baseline; other tracks and pitch data are preserved. Unidentifiable controls are omitted. If the whole fit has insufficient predicted improvement, the existing reference mapping is retained.

### Detailed parameter curves

The default path retains the original local fit's amplitudes and supported values, including variations within held notes. Dense sampling adds points on the original linear segments at approximately 5 ms intervals. Existing knots and the contour between them remain unchanged. Extra points do not add acoustic observations.

An audible, periodic analysis dropout of at most 80 ms can be interpolated inside continuous score coverage; rests and quiet or unvoiced gaps remain neutral. Long notes are not replaced with a note median or given a narrower amplitude range. The earlier conservative experiment remains available for explicitly selected profiles, but is no longer the default.

Reports record the input measurement interval, output spacing, interpolation method and bridged gaps under `detail`. Near-neutral unsupported regions are not filled with invented gestures. Constant neutral plateaus may omit redundant interior points while retaining their boundaries.

SVP export offers two sampling settings: **Keep original sampling** (`expression_density=original`) and **Dense sampling, 5 ms** (`dense`, the default). The setting is saved with presets, new task requests and export preferences; changing it invalidates SVP export only. CLI tasks accept `--expression-density original|dense`. Keeping original sampling does not thin an already dense source.

The dense pass runs after reference mapping, calibration or parameter restoration. It adds points to changing linear spans and existing controls during held notes, including flat held controls; it leaves long constant rests compact. Every input knot/value and the continuous piecewise-linear function are preserved, with tempo boundaries included. It uses linear interpolation rather than refitting a spline between linear source points. Empty controls acquire no new automation. Existing nonlinear editor curves keep their interpolation and are explicitly reported as unsupported, rather than counted as successfully densified. This pass does not modify notes, pitch, voice assignments, accompaniment or linked groups. It uses existing libraries and adds no model weights.

The output receipt records actual point counts, preserved knots and the largest interval between changing points under `expression_density`. Long constant rests are excluded from that interval statistic. Repeating the pass adds no more points and leaves the file unchanged. Restored curves therefore receive the same check as newly fitted curves, including files simplified by an editor save. Extra points interpolate the existing contour; they cannot recover acoustic detail absent from the source.

A single existing linear control point is sampled as a constant on known vocal-note spans; when no sampling span exists, the receipt says `no_sampling_span`. Finite source values outside the calibration solver's nominal range are preserved and labeled, rather than clipped or rejected. This does not widen the solver's generated-value bounds. Nonfinite values, malformed points and duplicate or reversed times still fail validation without replacing the source file.

Calibration also checks for suspicious pitch data, such as a mostly neutral contour interrupted only by exact semitone steps. This is a warning rather than proof that an intentional edit is wrong. Matching pitch data between two files does not establish its musical quality. Fixing pitch changes the score identity and requires new renders for a fresh measured-response calibration; simply reusing previous expression curves is a separate transfer experiment.

The SVP output's `calibration_fit` in `exports/report.json` records results. `predicted_reduction` is the local model's residual reduction, **not a listening score or measured improvement in a synthesized SV re-render**. `voicebank_calibrated` is true only if a track received a fit. Developer tests cover six-control recovery, bounds, correlated-response rejection, synthetic gain recovery, real WORLD features/cache and score/pitch preservation. Perceptual evaluation still requires actual editor renders.

No new weights or runtime packages are added beyond WORLD and existing numerical libraries. Small compressed feature caches live in the task's temporary directory. The seven WAVs can be large; start with a short score and retain renders while using the JSON. Changed notes or a new song require new probes. Large nonlinear control interactions and arbitrary timing changes are not fitted. Gender and Tone Shift follow their distinct meanings and units in the [official parameter manual](https://sv1.docs.dreamtonics.com/en/synthv/advanced-usage/parameters).

## Keeping expression when regenerating notes

A completed measured fit registers its actual parameter-bearing SVP with the music task. Reprocessing that task snapshots the result and carries its expression automation into the revised SVP, provided the source audio, timeline and note anchors still match. Notes and pitch come from the new extraction. Existing loudness, tension, breathiness, voicing, gender and Tone Shift curves retain their points and values.

This is preservation of an existing performance, not a fresh measured fit for changed notes. Newly recovered notes follow the existing timeline automation; a formerly neutral interval remains neutral. A newly selected measured-calibration profile takes precedence and retains its strict score checks. Source changes, missing curves or incompatible timing stop the export rather than silently producing a neutral replacement. Editor saves and expression-only edits can be carried forward when the score and voicebank remain the same.

## Pitch handling

New Vocal2Midi 2.0 tasks apply assistant-side pitch guards without changing installed upstream code or weights. Model input is scaled before exceeding its allowed range. Voicing masks exclude unsupported f0, long gaps return to neutral pitch control, and isolated one- or two-frame octave jumps are repaired only when the surrounding pitch agrees. Normal vibrato and sustained changes are preserved. These guards improve data handling; they do not establish a new note-transcription accuracy score. Existing completed tasks are preserved, and changed pitch processing invalidates only the affected inference cache.

## Evaluate

Compare the same score with the feature off and on, using the same voice, voice settings, effects and playback level. Listen for phrase dynamics and releases; also check whether compression, reverb or separation noise has caused unwanted level changes. Lower the strength or edit the curve where necessary. Preserve an original reference and a separate working copy of any manual tuning.

Developer regression coverage includes stereo/DC/gain invariance, silent audio, releases and phrase gaps, tempo changes and group offsets, cancellation, curve simplification, real SVP serialization and independent pitch/dynamics switches. Passing these checks establishes data handling; perceptual improvement still requires listening tests.
