# Simple mixing

In a task's “Files and next steps”, choose “Create simple mix”, select a vocal WAV rendered by the singing editor and an accompaniment, adjust the balance and vocal effects, preview, and export a new WAV. You can explicitly register the result as the task's Mixdown and continue to video assembly. Source files are never overwritten. The before preview retains level and timing but bypasses EQ, compression and reverb. Preview audio opens in the system player.

On first use, select “Install mixing component” in the mixing dialog. VWA downloads the pinned Pedalboard 0.9.26 Windows x64 wheel, verifies its size and SHA-256, extracts it under `dependencies/optional/pedalboard/current`, and probes its effects in an isolated subprocess. It does not modify the base Python site-packages. Download size is approximately 3.5 MiB; extracted runtime files use approximately 9.1 MiB. A network connection and write access to the application directory are required.

The pinned component and source are recorded in `components/pedalboard.json`; licence and static dependency details are in `components/pedalboard-THIRD_PARTY_NOTICES.md`. The existing v1.0.0 release predates this feature. Verify update compatibility and corresponding-source availability for the actual build before release; older updaters may require a compatible first hop or full package.
