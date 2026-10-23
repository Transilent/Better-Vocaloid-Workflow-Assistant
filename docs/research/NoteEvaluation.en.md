# Comparing transcription with human projects

[English](NoteEvaluation.en.md) | [简体中文](NoteEvaluation.zh-CN.md)

Human SVP projects can help locate missing ornaments, merged syllables, incorrect pitches and poor note boundaries. They serve as score references. They are not supplied to the recognizer or copied into its predictions.

## Preparing a comparison

Use the audible main melody, including referenced note-group instances, and convert its positions through the project's tempo map. Record the selected track, constant audio offset, transposition and input hashes. Confirm that the recording is the same arrangement and version; short videos, compilations and alternate covers can produce misleading comparisons.

Fix the audio excerpt and separation output before comparing algorithms. Exclude clipped edge notes and explicitly nonsung breath/silence notes from the scored reference, recording those exclusions. Keep ordinary continuation notes and short sung notes. Do not choose evaluation intervals or change alignment to favor a candidate after seeing its output.

Run the normal transcriber with reference lyrics empty when evaluating its automatic route. Keep raw predictions, measured pitch, syllable alignment and final notes. Replaying those fixed inputs isolates a post-processing change from stochastic model variation. A fresh full worker run is still needed to verify integration, cancellation, cache receipts and output formats.

## Metrics

The source-only research tool accepts a reference JSON containing a `notes` list. Each note has `onset`, `offset` in seconds and `pitch` as a MIDI key; `lyric` is optional. Other fields retain provenance.

```powershell
dependencies\vocal2midi\python\python.exe -B tools\research\note_benchmark.py work\reference.json work\prediction.mid work\note-metrics.json
```

Matching is one-to-one and maximizes the number of compatible pairs before minimizing timing distance. A duplicated prediction cannot claim credit twice. The report includes precision, recall and F1 at 50 ms and 100 ms onset tolerance, with pitch within half a semitone. Offset-aware scores additionally require an end within the larger of 50 ms or 20% of the reference duration. MIDI tempo changes are honored. Missing notes, extra notes, onset-matched wrong pitches and timing differences are retained for inspection.

These scores measure agreement with the selected written score. Human projects may encode a scoop as two notes or as a single note with automation; note onsets may also precede the acoustic vowel. Different notation is not automatically an audible error. Report strict and relaxed tolerances and inspect disagreements against the waveform and pitch curve.

## Current bounded repair

Vocal2Midi 2.0 note verification now measures fine pitch independently of whether an editor project or pitch curve is exported. Disabling curve export still removes explicit editor tuning. The installed recognition and pitch weights are unchanged.

Brief stable pitch changes can identify a potentially merged ornament. A contour-only split was rejected during development because it also split release bends. New short-step boundaries require local GAME inference to agree on both the boundary and the pitch centers on either side. The current worker uses its own audio and recognized alignment; it has no dependency on the reference collection. Existing long-melisma handling remains, with smooth periodic wide vibrato excluded from extra note creation. Discrete step transitions remain eligible, and the measured continuous pitch is preserved.

Optional local checks share the collapsed-syllable recovery budget of 16 windows and 24 seconds per voice. They reuse the existing model. Unconfirmed proposals or a failed optional inference preserve the original notes; cancellation propagates. The quality receipt records attempted checks. This conservative policy leaves some clearly audible pickups unrecovered when GAME cannot confirm them.

Local confirmation also checks the original continuous GAME pitch centers against measured pitch. Rounding both estimates first can hide nearly a semitone of disagreement. An intermediate bend near a half-semitone boundary is insufficient evidence for an extra note; the new GAME centers must lie within 0.3 semitones of their keys and within 0.45 semitones of the measured local centers. These checks affect note creation, not the retained pitch contour.

Small development comparisons establish specific regressions, not a general accuracy percentage. Keep unsuccessful candidates and evaluation roles in the research record. A song inspected while changing a rule is no longer an untouched holdout. Chinese and Japanese must be evaluated separately before claiming improvements for both languages; retaining their existing ASR routes does not establish equal accuracy.
