# Unified preset documents and local library

[简体中文](PresetLibrary.zh-CN.md) | [User guide](UserGuide.en.md)

**Settings and support → Preset library** manages preparation, mixing, tuning, style recipes and voicebank calibrations. The Tuning Workshop offers a focused view of styles and calibrations in the same library. Six mixing presets and four preparation workflows can be copied into new user presets.

The manager stores data. The mixing console offers save, choose and undo. In tuning, you can save whole-track plans, preview changes, bind local project inputs, and generate candidates with imported style recipes. Imports append by default. Selecting a style requires separate confirmation in the tuning workspace.

## Using and sharing

Import a local `.vwa-preset.json` after reviewing its name, type and version. Export individual files for sharing through GitHub or another platform. The Workshop can capture style envelopes and pitch gestures, or save a measured voicebank calibration. Aliases and favorites are local metadata. When editing a shared file, assign a new version to changed parameters. Built-in exports are imported as independent copies. Use Create independent copy or `import --as-copy` to retain both versions when an identity conflicts. Copies use a new ID and version `0.1.0`.

The examples cover [preparation](examples/presets/preparation.vwa-preset.json), [mixing](examples/presets/mixdown.vwa-preset.json), [tuning](examples/presets/tuning.vwa-preset.json), and [style recipes](examples/presets/style_recipe.vwa-preset.json).

## Using presets in the mixing console

Save current as preset captures EQ, compression, reverb and configured per-vocal de-essing, including retained values of bypassed controls. Each save appends a new identity, even with the same display name. Overall output width is optional. The preset stores processing settings.

Choose from local library shows a before/after comparison without changing the mix. Manage presets can append imported files and refresh the list. Applying output width and declared track settings are separate opt-ins. De-essing applies to the corresponding vocal slots in order. Tracks without a matching entry keep their current settings.

Confirm apply rechecks the selected version/digest and live mixer state. EQ frequencies respect the current sample rate and the preview reflects actual percentage/predelay control precision without rewriting the package. One-step undo is available until the next manual parameter edit. Existing audition and export processing use the resulting controls.

## Tuning plans and local bindings

Save a whole-track plan from the tuning controls, load a target SVP, then choose a library entry to preview changes and select vocal tracks. Reference mode needs per-track audio and explicit voice roles; calibrated mode also needs a matching score/voicebank calibration profile. Retained mode needs the source SVP and track mapping. An incomplete plan can be applied to the form, but generation stays disabled until its inputs are supplied. Shareable documents exclude local paths.

Application rechecks the project SHA, exact preset bytes and current form. Generation and recovery reuse the existing immutable-input candidate transaction. Receipts freeze the preset document, identity and local bindings, so restoring a session does not require the library entry to remain present. Manual processing edits detach the preset identity. Recognized candidates require explicit confirmation before becoming a new baseline. See the [Chinese operation and architecture guide](TuningPresets.zh-CN.md).

Open a project, import the recipe in **Presets**, review its channels and default strength, then choose **Select style**. Adjust the strength in **Style**. Use **Apply to time range** for a specific passage, then preview and generate a candidate. Saving a whole-track tuning plan with an imported style records the recipe as a dependency.

## Version 1 contract

Files are UTF-8 JSON (optional BOM), at most 256 KiB and 16 levels deep. Unknown fields, duplicate keys, non-finite numbers, malformed text, scripts, raw project paths and device bindings are rejected. Descriptive metadata is never executed as a path, URL or command.

The required envelope is `format: "vwa.preset"`, integer `format_version: 1`, `id`, `type`, `version`, `metadata`, `requires`, `binding_slots`, and `payload`. IDs use lowercase ASCII namespaces such as `user.example.clear_vocal` (128 characters maximum); `builtin.*` is reserved. Versions use full SemVer, such as `0.1.0` or `1.0.0-rc.1`.

| Kind | Portable content |
| --- | --- |
| `preparation` | A supported graph and 16 explicit portable options. External stems use a required input slot. Paths, lyrics, hardware and thread settings are excluded. |
| `mixdown` | Four ordered EQ bands (high-pass, low shelf, bell, high shelf), bypass flags and retained values, compression, reverb and optional track template. Optional `vocal_deess` lists one to three pairs of `enabled` and `strength` in vocal order and declares `mix.deess`. Overall output width requires an explicit scope. |
| `tuning` | One expression source, style/technique references and strengths, density, and required target/source slots. No live project bindings. |
| `style_recipe` | V1 and v2 use 2–64 additive control points per channel on normalized note phase. V2 supports local pitch edges. V3 (`style.phrase-structure`) stores note and phrase envelopes with optional phrase-entry, note-transition, phrase-end and guarded long-note vibrato gestures. Expression fields use ±12 dB for loudness and ±1 for other channels. |
| `voicebank_calibration` | Target SVP score and voicebank settings fingerprints, fit strength and seven trial WAV fingerprints. Link the original trial folder and choose a reference vocal when applying it. |

`requires` declares the API, engine contract, capabilities, and dependencies. Tuning plans can resolve an installed v1 recipe with the `style` role by its exact `id + version + sha256`. Share the dependency file alongside any plan that references it. The receiver imports both original files before binding the plan. V1 style recipes apply expression envelopes. Pitch gestures and external techniques require a future execution contract.

The [JSON Schema](../packaging/preset.schema.json) supports editor validation. Full validation uses `preset_documents.validate_document`: runtime checks additionally enforce duplicate-key rejection, byte/depth limits, strict integers, nonblank text and cross-field graph, slot, scope and dependency rules. Keep schema, examples and validation aligned when changing supported fields.

## Persistence and recovery

The default `user-presets/` directory is separate from configuration, jobs and projects. Read-only access does not create it. Portable packages exclude it, and application updates preserve it. Back up the whole directory.

Versions retain original bytes and SHA-256. The same ID, version and raw digest is idempotent; different bytes, including whitespace changes, require a new version. Versions coexist. Copy creates a new `user.*` ID at `0.1.0`. Delete removes the current entry while retaining immutable documents and recovery history; it does not immediately reclaim space.

Writes share a cross-process sidecar lock and expected revision. Stale operations fail; refresh and confirm again. Content is stored before the index is atomically replaced. Broken indexes/documents produce explicit errors. **Recover previous index** preserves damaged evidence and restores the most recent valid predecessor; it does not repair damaged content or recover arbitrary history. Library/import/export paths cannot traverse symbolic links or Windows reparse points. Use an ordinary local directory.

## CLI

Run the existing Python from a prepared application directory:

```powershell
python -B preset_cli.py list
python -B preset_cli.py example mixdown
python -B preset_cli.py validate .\my.vwa-preset.json
python -B preset_cli.py import .\my.vwa-preset.json
python -B preset_cli.py export user.example.clear_vocal 0.1.0 .\shared.vwa-preset.json
python -B preset_cli.py --root C:\VWA-Presets list
```

Also available: `schema`, `show`, `copy --name`, `rename --alias`, `favorite on|off`, `delete`, `recover`. Results are JSON; errors return exit code 2. GUI and CLI use the same format and transaction rules.
