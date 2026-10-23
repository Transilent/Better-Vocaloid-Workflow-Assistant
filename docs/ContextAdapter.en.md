# Explicit context audition projects

The automatic semantic-recognition direction was retired on 2026-10-20. This explicit adapter and its receipts remain useful for manually selected, reviewed local experiments; existing semantic A/B files are historical candidates. New density-only comparisons contain no semantic recipe changes. Production SVP export offers `expression_density=original|dense`; the latter preserves the original linear contour and samples existing controls during held notes at 5 ms, without inventing empty controls.

[简体中文](ContextAdapter.zh-CN.md)

The context project CLI prepares independently recoverable A/B SVP files. A preserves the supplied expressive project. B applies a reviewed selection of local onset, sustain, or release gestures through an explicit profile. This is an experimental audition facility; a valid file does not establish a voicebank response, semantic accuracy, or better sound.

Run the generated modules from the application root using the bundled Python. Do not launch `src/launch.py`.

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' project prepare --project 'song.svp' --lyrics 'lyrics.json' --language ja --output 'analysis'
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' project apply --bundle 'analysis' --application 'application.json' --profile 'profile.json' --output 'audition'
```

`lyrics.json` maps `lead`, `backing`, and any additional reported voice keys to original Unicode text. Missing voice text stays unknown. `--words` optionally supplies explicit word spans by voice. `--readings` optionally supplies source-backed original spans and reading tokens by voice. Japanese can use the already installed OpenJTalk dictionary offline; a missing dictionary produces an error rather than a download. No ASR model is loaded. Japanese ownership remains at the frontend surface-word level when readings cannot be assigned to individual kanji.

Analysis creates `baseline.svp`, `alignment.json`, `evidence.json`, `readings.json`, `proposal.json`, `inputs.json`, and a hash manifest. Ordinary callers of the older evidence API retain its strict whole-sequence behavior. The new opt-in alignment accepts only unambiguous ordered token associations, keeps mismatches/omissions local, and preserves ambiguity in repeated material. This is text/reading evidence, not an acoustic alignment claim.

An application explicitly references the analysis evidence IDs, voice-to-track bindings, selected note IDs, source span/quote, phase, recipe, amount, and rationale. A profile names the intended voicebank, declares whether an unbound bank is allowed, and provides numerical deltas for its phase-specific recipes. Its required status is `experimental_unrendered`. Free-form emotion words do not select numerical recipes. Profiles are separate from measured calibration responses; do not reuse a score-specific calibration across unrelated projects.

Only loudness, tension, breathiness, and voicing can be changed. The adapter uses bounded local envelopes, preserves all original point time coordinates, and samples changed regions densely. Density adds samples on the existing piecewise-linear function; it does not refit a spline between original points. The selected regions intentionally have changed values; the original A remains complete. Unselected continuous values, notes, pitch curves, unknown parameters, long-note fluctuations, voice settings, retakes, mixer, and accompaniment remain intact. Conflicting gestures, windows covering unselected simultaneous notes, stale evidence, wrong track bindings, unsupported selected interpolation, and out-of-range values fail explicitly. Flat untrimmed note groups are supported; linked or trimmed groups are rejected rather than partially processed.

Successful application creates `A-before.svp`, `B-context-trial.svp`, the exact application/profile, and a receipt. Both candidate artifacts are re-readable SVPs. Existing outputs are reused only when inputs and every registered output hash match; changed outputs are never overwritten. Interrupted staging folders do not count as completed results. Original task requests, registered projects, login state, and source SVPs are not modified.

Load A and B with the same voicebank, engine version, vocal mode, mixer, and effects. Render in Synthesizer V and compare the reviewed passages in context. Functional checks, acoustic observations, and listening preferences are separate results. No synthesis or listening occurs in this CLI, and semantic model services remain optional explicit interfaces.
