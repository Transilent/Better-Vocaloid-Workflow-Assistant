# Update compatibility

[English](UpdateProtocol.en.md) | [简体中文](UpdateProtocol.zh-CN.md) | [Verification](Verification.en.md) | [README](../README.md)

This document records update and naming compatibility in the development source. It does not announce a release or imply that every described asset is available.

## Canonical names

`VWA.exe` is the canonical application launcher and `VWA-Updater.exe` is the canonical one-time compatibility updater. Fresh complete packages contain `VWA.exe`; they do not include a `BVWA.exe` launcher.

When the updater migrates an eligible existing installation that already contains the released `BVWA.exe` launcher, it may retain a hidden `BVWA.exe` shortcut alias. The alias uses the same launcher bytes so existing shortcuts can open the upgraded application. It is never added to a fresh portable package and is not a second application.

The VWA name is a product and package rename. The remote repository remains `Transilent/Better-Vocaloid-Workflow-Assistant`, including its Release URLs. Update identifiers that released clients consume also remain available in the compatibility layer.

## Portable packages and metadata

Canonical VWA package discovery uses `portable_downloads.full` and `portable_downloads.jpn` in the release catalog. These entries identify `VWA-Windows-x64.zip` and `VWA-Windows-x64-JPN.zip` respectively.

Split packages have variant-specific metadata. Use `portable-metadata-full.json` with the canonical full parts and `portable-metadata-jpn.json` with the JPN parts. The helper supplied with each package verifies those matching parts and metadata.

The top-level `archive` and `parts` fields in `release-assets.json` are retained as the metadata for a BVWA-root legacy compatibility full ZIP. The frozen `tools/Install-Runtime.ps1` path still consumes that legacy projection. Do not use those fields to identify canonical VWA full or JPN parts.

## Update inventory and state

`release-assets.json` continues to identify a supported base runtime by the SHA-256 of its installed `dependencies/manifest.json`. A version label alone does not identify the runtime. The `updates` array provides one route per source runtime and records the first-hop archive, runtime/program delta, sizes and SHA-256 values.

Reused runtime files are checked by size and SHA-256 before staging and again before installation. The current installed version prevents stale plans from downgrading or repeating an installed target. A matching `upgrade_pending` marker is the only same-version exception, allowing a verified first hop to finish.

Private state schemas written by released clients remain readable. Compatibility identifiers remain where installed clients expect them, so the visible rename does not require a destructive reset of settings, tasks or sign-in profiles. Unchanged model files remain in place.

## Frozen first hop and legacy aliases

The compatibility first hop is frozen to exactly three files: `app.py`, `version.json`, and `tools/update_apply.py`. `version.json` binds that small archive to the target version, while the replacement `app.py` starts the migration window before the newer display modules are installed. The first hop verifies itself, prepares the matching runtime/program delta, and installs the rest of the transaction before one restart.

Canonical bootstrap archives can also be published through optional BVWA-named alias files whose bytes, sizes and SHA-256 values match the canonical archive. These aliases exist only for clients that cannot read the new filename. The legacy top-level fixed update fields continue to name the BVWA archives:

| Installed client | Compatibility entry |
| --- | --- |
| Current VWA client | Selects a runtime-hash route from `updates` |
| Released 0.4.1 client | `update`: `BVWA-App-Update.zip`, followed by the migration window |
| Released 0.4.0 client | `program_update`: `BVWA-Program-Update.zip`, followed by the migration window |
| 0.3.0 / 0.3.1 | One-time `VWA-Updater.exe`, when supplied by the official release |

The fixed BVWA filenames are intentional legacy protocol names. They are not the names of a fresh installation or of the canonical VWA package.

## Locks and installation safety

The application and compatibility paths use dual locks. `cache/BVWA.lock` and `cache/VWA.lock` are acquired together before startup or update preparation. If either lock is busy, any lock already acquired is released and the operation stops. This interlock prevents a canonical launcher and an upgraded shortcut alias from running separate instances.

Installation also respects active task, publication-browser, component-installation and download locks. Files are staged and verified before replacement, backups are retained during the transaction, and a failed replacement restores the previous files. The updater never forcibly closes an active user process.

## Compatibility updater

Download `VWA-Updater.exe` only from the official release that supplies it. Close the assistant and its processing or upload windows, then run the tool next to the installed `VWA.exe` or select the existing application folder when prompted. An upgraded installation may still be located through its retained `BVWA.exe` alias.

Choose GitHub directly, GitProxy, or automatic fallback. Check for updates, download the verified delta, then choose **Install and launch**. The tool does not publish content or perform periodic background checks. Errors are recorded in `cache/updates/compatibility.log`; installation results are recorded in `cache/updates/last-result.json`.

## Maintainer checks

1. Keep `packaging/update-clients.json` and the released-client fixtures and hashes unchanged.
2. Preserve the first-hop allowlist as exactly `app.py`, `version.json`, and `tools/update_apply.py`; do not add new display modules to that hop.
3. Publish canonical VWA packages through `portable_downloads.full` and `portable_downloads.jpn`. For split delivery, publish the matching full or JPN portable metadata and header without mixing variants.
4. Keep top-level `release-assets.json` `archive` and `parts` compatible with the frozen BVWA-root `Install-Runtime` path. That projection is separate from canonical VWA package discovery.
5. Keep `BVWA-App-Update.zip` and `BVWA-Program-Update.zip` as the fixed top-level legacy update names. Any BVWA-named bootstrap alias must be byte-identical to its canonical archive.
6. Verify that a fresh package contains `VWA.exe` only and that a hidden `BVWA.exe` alias is created solely during migration of an existing installation.
