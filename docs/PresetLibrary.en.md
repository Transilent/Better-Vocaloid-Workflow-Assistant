# Unified preset documents and local library

[简体中文](PresetLibrary.zh-CN.md) | [User guide](UserGuide.en.md)

The development version provides **Settings/updates → Local preset library** for preparation, mixing, tuning and style recipes. Inspect and confirm imports, copy built-ins, add local aliases/favorites, delete entries and export files. Six existing mixing presets and four preparation workflows are read-only; copy one to create your own identity.

This stage manages data. Saving current controls and applying library entries in mixing/tuning pages are future work. Importing does not change a project or its sound. Style recipes are stored without execution. Online catalogs, downloads and GitHub account integration are deferred.

## Using and sharing

Import a local `.vwa-preset.json` after reviewing its name, kind, version and JSON. Export individual files and share them manually through GitHub or another platform. Existing destination files are never overwritten. Aliases and favorites affect only the local index and preserve the imported package and digest. To change parameters, edit a conforming example/export and assign a new version; an in-app parameter editor is deferred. Built-in exports retain reserved `builtin.*` identities for inspection/archive. Copy them first when producing importable shared variants.

Examples: [preparation](examples/presets/preparation.vwa-preset.json), [mixing](examples/presets/mixdown.vwa-preset.json), [tuning](examples/presets/tuning.vwa-preset.json), [style recipe](examples/presets/style_recipe.vwa-preset.json). Validation does not imply application adapters are available.

## Version 1 contract

Files are UTF-8 JSON (optional BOM), at most 256 KiB and 16 levels deep. Unknown fields, duplicate keys, non-finite numbers, malformed text, scripts, raw project paths and device bindings are rejected. Descriptive metadata is never executed as a path, URL or command.

The required envelope is `format: "vwa.preset"`, integer `format_version: 1`, `id`, `type`, `version`, `metadata`, `requires`, `binding_slots`, and `payload`. IDs use lowercase ASCII namespaces such as `user.example.clear_vocal` (128 characters maximum); `builtin.*` is reserved. Versions use full SemVer, such as `0.1.0` or `1.0.0-rc.1`.

| Kind | Portable content |
| --- | --- |
| `preparation` | A supported graph and 16 explicit portable options. External stems use a required input slot. Paths, lyrics, hardware and thread settings are excluded. |
| `mixdown` | Four ordered EQ bands (high-pass, low shelf, bell, high shelf), independent bypass flags and retained values, compression, reverb and optional track template. Overall output width requires an explicit scope because it can affect accompaniment. |
| `tuning` | One expression source, style/technique references and strengths, density, and required target/source slots. No live project bindings. |
| `style_recipe` | A `style` or `technique` role and additive curves over normalized note phase 0–1. Loudness, tension, breathiness and voicing only, 2–64 points per channel, strictly increasing from phase 0 to 1. Loudness deltas ±12; other channels ±1. Pitch recipes must be disabled in v1. |

`requires` declares the API, matching engine contract and capability names. External style/technique references pin `id + version + sha256` and must exactly match dependencies. Storage accepts unresolved dependencies without fetching or applying them. Future adapters must check capabilities, reference kind/role, digest and project inputs before offering a change preview.

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
