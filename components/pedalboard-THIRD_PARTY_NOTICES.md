# Pedalboard 0.9.26 third-party component notice

This notice covers the optional Pedalboard component identified by
`components/pedalboard.json`. The current base VWA archive and the existing
v1.0.0 binaries were built before this component entry and do not contain this
Pedalboard wheel. Recording the component here does not assert that any
already-published binary satisfies Pedalboard's or its dependencies' licence
conditions.

## Artifact and upstream source

- Wheel: `pedalboard-0.9.26-cp312-cp312-win_amd64.whl`
- Wheel SHA-256: `816b7063d3befb00263bfee7656932ee5f51788c586d5184d4d61f7a85f61aea`
- Pedalboard source: <https://github.com/spotify/pedalboard/tree/05e8f0b3c4851e77485d7e842dc0355b44106471>
- Upstream tag: `v0.9.26`

Pedalboard itself is Copyright 2021-2026 Spotify AB and is licensed under the
GNU General Public License, version 3. A verbatim copy is retained in
`components/pedalboard-LICENSE.txt`; the upstream package notice is retained in
`components/pedalboard-NOTICE.txt`.

## Code compiled into the Windows extension

The 0.9.26 build configuration compiles the following components into
`pedalboard_native.cp312-win_amd64.pyd`:

| Component | Revision or bundled version | License / attribution |
| --- | --- | --- |
| JUCE | 6.1.4, commit `ddaa09110392a4419fecbb6d3022bede89b7e841` | Dual-licensed commercial/GPLv3; Pedalboard uses the GPLv3 option. Individual modules also carry their upstream notices, including ISC for some JUCE modules. |
| VST3 SDK | Bundled in the JUCE revision above | Dual-licensed Steinberg proprietary/GPLv3; the GPLv3 option applies to the Pedalboard build. Copyright Steinberg Media Technologies GmbH. |
| Rubber Band Library | commit `2be46b0dffb13273a67396c77bc9278736bb03d2` | Dual-licensed commercial/GPLv2 or later; Pedalboard uses the GPL-compatible option. |
| libmp3lame and mpglib (LAME) | commit `1f5cc9487284d5950343aa5d4f70de433468070a` | libmp3lame is LGPL-2.0 upstream; Pedalboard documents incorporating it under GPLv3 as permitted by the LGPL. mpglib is GPL. |
| libgsm | commit `98f1708fb5e06a0dfebd58a3b40d610823db9715` | ISC-style permission notice from Jutta Degener and Carsten Bormann / Technische Universitaet Berlin. |
| libFLAC | Bundled with JUCE | Xiph.Org BSD-style license, copyright Josh Coalson and Xiph.Org Foundation. |
| Ogg Vorbis (libogg/libvorbis) | Bundled with JUCE | Xiph.Org BSD-style license. |
| dr_wav | Bundled by Pedalboard | Public domain / MIT-0, by David Reid. |
| pybind11 | commit `0c69e1eb2177fa8f8580632c7b1f97fdb606ce8f` | BSD-3-Clause; binding support compiled into the extension. |

The upstream Pedalboard NOTICE also mentions AnalogTapeModel test binaries.
Those test assets are not included in this Windows wheel. The Windows CMake
build uses its built-in FFT path rather than compiling the vendored FFTW
sources; the generic upstream README mentions FFTW for platforms where it is
linked. A final release must verify the actual binary and generated software
bill of materials instead of inferring compliance from the project name or the
GPLv3 label alone.

## Redistribution gate

A future package that distributes this wheel or a binary built from it must
preserve the wheel and dependency notices, provide the applicable GPLv3 terms,
and provide the corresponding source or a valid written source offer for the
exact distributed build. Any separate commercial licensing choice for JUCE,
VST3, Rubber Band, or another dual-licensed dependency requires its own valid
agreement. This file is a technical inventory, not a statement that an existing
VWA release is compliant.