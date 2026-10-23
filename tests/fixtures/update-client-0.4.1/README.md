# Released-client contract fixture

These two files are unmodified snapshots of the updater and installer shipped in
BVWA 0.4.1, commit `43ea3f5b11a56504c111909beb813f762857e6d3`.
Source URLs and SHA-256 hashes are recorded in `packaging/update-clients.json`.

They are test inputs, not application modules. Keep their historical behavior:
the compatibility test must prove that an unmodified old client accepts the
small installer-upgrade package before any newly introduced module is sent.
Fixes belong in `src/updater.py` and `tools/update_apply.py`.
