# VWA Release Signing & Supply-Chain Verification Guide

## 1. Automated Supply-Chain Artifacts (Included in Every Release)

Running `tools/build_portable.py` (`write_release_metadata`) automatically generates:

- `sbom.cdx.json`: CycloneDX 1.5 Software Bill of Materials enumerating pinned Python runtime dependencies (`packaging/requirements-runtime.lock.txt`), PySide6/Qt6 version, and native C/C++ libraries (`miniaudio`, `signalsmith-stretch`, `signalsmith-linear`, `FFmpeg alimiter`).
- `SHA256SUMS.txt`: Coreutils-compatible SHA-256 checksums for all release archives, split volumes, update packages, and `sbom.cdx.json`.

Users can verify downloads locally on Windows PowerShell:

```powershell
Get-Content SHA256SUMS.txt | ForEach-Object {
    $expected, $file = $_ -split '\s+', 2
    if (Test-Path $file) {
        $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected.ToLowerInvariant()) { throw "Checksum mismatch: $file" }
        Write-Host "OK: $file"
    }
}
```

## 2. Open-Source Code Signing via SignPath Foundation

To eliminate Windows SmartScreen "Unknown Publisher" warnings on `VWA.exe`, native DLLs, and `VWA-Setup-*.exe` without purchasing a commercial EV certificate:

1. Apply for the free **SignPath Foundation** program for open-source projects (https://signpath.org/) linking the public GitHub repository (`Transilent/Better-Vocaloid-Workflow-Assistant`).
2. Configure the SignPath project to accept artifacts **only** from GitHub Actions release workflows building from tagged commits (`v*.*.*`).
3. Sign the following binaries before zipping / Inno Setup compilation:
   - `VWA.exe`
   - `components/native-audio/vwa_native_output.dll`
   - `components/native-audio/vwa_limiter.dll`
   - `components/native-audio/vwa_chorus_pitch.dll`
   - `VWA-Setup-<version>-x64.exe` (when publishing the Inno Setup installer)
4. Verify signatures locally prior to publishing the GitHub Release:
   ```powershell
   Get-AuthenticodeSignature VWA.exe, components\native-audio\*.dll | Format-Table Path, Status, SignerCertificate
   ```
