[CmdletBinding()]
param(
    [string]$GccPath = 'D:\mingw64\bin\gcc.exe',
    [string]$MiniaudioHeaderPath = '',
    [string]$OutputDirectory = '',
    [switch]$RunTests,
    [switch]$ProbeWasapi
)

$ErrorActionPreference = 'Stop'
$utilityManifest = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1'
if (Test-Path -LiteralPath $utilityManifest) {
    Import-Module -Name $utilityManifest -Force
}
$pinVersion = '0.11.26'
$pinCommit = '877ea4a7c2601c7b0f2a44e83dfac42e6470fea6'
$pinHeaderSha256 = '6B376C7FBB1352BA1E0E432AB66069B335EA9405E45CB6C30B97EF0F3C9C3E0E'
$pinLicenseSha256 = '457F1B500E0ADF6BC059EDDDFA78A2F62012E7C3BB43476C20E0BD23B25BA0EB'
$startedUtc = [DateTime]::UtcNow.ToString('o')

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $repoRoot
$projectRoot = Split-Path -Parent $workspaceRoot
$repoNativeWork = Join-Path $repoRoot 'work\native-audio'
$maintenanceNativeWork = Join-Path $projectRoot 'work\audition-native-device-20261108\native'
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoNativeWork 'build'
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

if (-not (Test-Path -LiteralPath $GccPath)) {
    throw "GCC not found at explicit local path: $GccPath"
}
$gcc = Get-Item -LiteralPath $GccPath
$gccVersion = @(& $gcc.FullName --version)[0]
if ($LASTEXITCODE -ne 0) {
    throw "failed to query GCC version: $($gcc.FullName)"
}

if (-not $MiniaudioHeaderPath) {
    $candidates = @(
        (Join-Path $repoNativeWork 'third_party\miniaudio\miniaudio.h'),
        (Join-Path $maintenanceNativeWork 'third_party\miniaudio\miniaudio.h'),
        (Join-Path $projectRoot 'work\audition-implementation-20261108\native-prototype\third_party\miniaudio\miniaudio.h')
    )
    $MiniaudioHeaderPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $MiniaudioHeaderPath -or -not (Test-Path -LiteralPath $MiniaudioHeaderPath)) {
    throw 'miniaudio.h not found. Pass -MiniaudioHeaderPath with the existing pinned local 0.11.26 header.'
}
$header = Get-Item -LiteralPath $MiniaudioHeaderPath
$headerHash = (Get-FileHash -LiteralPath $header.FullName -Algorithm SHA256).Hash
if ($headerHash -ne $pinHeaderSha256) {
    throw "miniaudio header pin mismatch: expected $pinHeaderSha256, got $headerHash"
}
$license = Join-Path $header.DirectoryName 'LICENSE'
$licenseHash = $null
if (Test-Path -LiteralPath $license) {
    $licenseHash = (Get-FileHash -LiteralPath $license -Algorithm SHA256).Hash
    if ($licenseHash -ne $pinLicenseSha256) {
        throw "miniaudio LICENSE pin mismatch: expected $pinLicenseSha256, got $licenseHash"
    }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$dllPath = Join-Path $OutputDirectory 'vwa_native_output.dll'
$testExePath = Join-Path $OutputDirectory 'vwa_native_output_test.exe'
$commonArgs = @(
    '-std=c11',
    '-O2',
    '-Wall',
    '-Wextra',
    '-Wpedantic',
    '-Wno-unused-function',
    "-I$(Join-Path $repoRoot 'src/native')",
    "-I$($header.DirectoryName)"
)

& $gcc.FullName @commonArgs '-DVWA_OUTPUT_BUILD_DLL' '-shared' (Join-Path $repoRoot 'src/native/vwa_native_output.c') `
    '-o' $dllPath '-static-libgcc' '-Wl,--strip-all' '-lole32' '-luuid' '-luser32'
if ($LASTEXITCODE -ne 0) {
    throw "native DLL build failed: $LASTEXITCODE"
}
& $gcc.FullName @commonArgs (Join-Path $repoRoot 'src/native/vwa_native_output.c') (Join-Path $repoRoot 'src/native/test_vwa_output.c') `
    '-o' $testExePath '-static-libgcc' '-Wl,--strip-all' '-lole32' '-luuid' '-luser32'
if ($LASTEXITCODE -ne 0) {
    throw "native test executable build failed: $LASTEXITCODE"
}

function Write-Utf8Text([string]$Path, [string]$Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

$nullTestPath = Join-Path $OutputDirectory 'null-test.json'
$nullExit = $null
$nullPassed = $null
if ($RunTests) {
    $nullText = (@(& $testExePath '--null-json' 2>&1) | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    $nullExit = $LASTEXITCODE
    Write-Utf8Text $nullTestPath ($nullText + [Environment]::NewLine)
    try {
        $nullPassed = [bool](ConvertFrom-Json $nullText).passed
    } catch {
        $nullPassed = $false
    }
    if ($nullExit -ne 0 -or -not $nullPassed) {
        throw "native null test failed: exit $nullExit (receipt: $nullTestPath)"
    }
}

$probePath = Join-Path $OutputDirectory 'wasapi-silent-probe.json'
$probeExit = $null
$probePassed = $null
$probeResult = $null
if ($ProbeWasapi) {
    $probeText = (@(& $testExePath '--wasapi-silent-json' 2>&1) | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    $probeExit = $LASTEXITCODE
    Write-Utf8Text $probePath ($probeText + [Environment]::NewLine)
    try {
        $probeJson = ConvertFrom-Json $probeText
        $probePassed = [bool]$probeJson.passed
        $probeResult = [string]$probeJson.result
    } catch {
        $probePassed = $false
        $probeResult = 'invalid_probe_receipt'
    }
}

$objdumpPath = Join-Path $gcc.DirectoryName 'objdump.exe'
$imports = @()
if (Test-Path -LiteralPath $objdumpPath) {
    $imports = @(& $objdumpPath -p $dllPath 2>&1 | Where-Object { $_ -match 'DLL Name:' } | ForEach-Object { ($_ -split ':', 2)[1].Trim() })
}

$sourceHashes = @{}
foreach ($relative in @('src/native/vwa_native_output.h', 'src/native/vwa_native_output.c', 'src/native/test_vwa_output.c', 'src/mixdown_native_output.py')) {
    $sourceHashes[$relative] = (Get-FileHash -LiteralPath (Join-Path $repoRoot $relative) -Algorithm SHA256).Hash
}

$receipt = [ordered]@{
    schema = 'vwa.mixdown_native_output.build.v1'
    result = 'built'
    started_utc = $startedUtc
    finished_utc = [DateTime]::UtcNow.ToString('o')
    source_root = $repoRoot
    output_directory = $OutputDirectory
    network_used = $false
    dependency_installed = $false
    compiler = [ordered]@{ path = $gcc.FullName; version = $gccVersion }
    miniaudio = [ordered]@{
        version = $pinVersion
        tag_commit = $pinCommit
        header_path = $header.FullName
        header_sha256 = $headerHash
        license_path = if ($licenseHash) { $license } else { $null }
        license_sha256 = $licenseHash
    }
    artifacts = [ordered]@{
        dll = [ordered]@{ path = $dllPath; sha256 = (Get-FileHash -LiteralPath $dllPath -Algorithm SHA256).Hash }
        test_exe = [ordered]@{ path = $testExePath; sha256 = (Get-FileHash -LiteralPath $testExePath -Algorithm SHA256).Hash }
    }
    sources_sha256 = $sourceHashes
    dll_imports = $imports
    null_test = [ordered]@{ requested = [bool]$RunTests; receipt = if ($RunTests) { $nullTestPath } else { $null }; exit_code = $nullExit; passed = $nullPassed }
    silent_wasapi_probe = [ordered]@{
        requested = [bool]$ProbeWasapi
        receipt = if ($ProbeWasapi) { $probePath } else { $null }
        exit_code = $probeExit
        passed = $probePassed
        result = $probeResult
        note = 'Device unavailable is recorded as blocked/failed, never as a pass. Probe is paused zero-fill only.'
    }
}
$receiptPath = Join-Path $OutputDirectory 'build-receipt.json'
Write-Utf8Text $receiptPath (($receipt | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
Write-Output $receiptPath
