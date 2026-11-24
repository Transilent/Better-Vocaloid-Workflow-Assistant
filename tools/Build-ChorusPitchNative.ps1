[CmdletBinding()]
param(
    [string]$GxxPath = 'D:\mingw64\bin\g++.exe',
    [string]$StretchSourceDir = '',
    [string]$LinearIncludeDir = '',
    [string]$OutputDirectory = '',
    [switch]$InstallForDevelopment
)

$ErrorActionPreference = 'Stop'
$pinStretchCommit = 'a670068d9aeb64913331d5cc29337b19a457a7df'
$pinLinearCommit = 'de55e6a50ffcf6f8f43f649692d94691c7025151'
$startedUtc = [DateTime]::UtcNow.ToString('o')

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $repoRoot
$projectRoot = Split-Path -Parent $workspaceRoot
$upstreamRoot = Join-Path $projectRoot 'work\vwa-chorus-oss-20261122\upstream'

if (-not $StretchSourceDir) {
    $StretchSourceDir = Join-Path $upstreamRoot "signalsmith-stretch-$pinStretchCommit"
}
if (-not $LinearIncludeDir) {
    $LinearIncludeDir = Join-Path $upstreamRoot "linear-$pinLinearCommit\include"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot 'work\native-pitch\build'
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

if (-not (Test-Path -LiteralPath $GxxPath)) {
    throw "g++ not found at explicit local path: $GxxPath"
}
$stretchHeader = Join-Path $StretchSourceDir 'signalsmith-stretch.h'
$stftHeader = Join-Path $LinearIncludeDir 'signalsmith-linear\stft.h'
if (-not (Test-Path -LiteralPath $stretchHeader)) {
    throw "signalsmith-stretch.h not found at $stretchHeader"
}
if (-not (Test-Path -LiteralPath $stftHeader)) {
    throw "signalsmith-linear/stft.h not found at $stftHeader"
}

$gxxVersion = @(& $GxxPath --version)[0]
if ($LASTEXITCODE -ne 0) {
    throw "failed to query g++ version: $GxxPath"
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$dllPath = Join-Path $OutputDirectory 'vwa_chorus_pitch.dll'
$sourceCpp = Join-Path $repoRoot 'src\native\vwa_chorus_pitch.cpp'

& $GxxPath '-std=c++17' '-O3' '-Wall' '-Wextra' '-shared' '-static' `
    "-I$StretchSourceDir" "-I$LinearIncludeDir" $sourceCpp '-o' $dllPath '-Wl,--strip-all'
if ($LASTEXITCODE -ne 0) {
    throw "chorus pitch DLL build failed: $LASTEXITCODE"
}

function Write-Utf8Text([string]$Path, [string]$Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

$dllHash = (Get-FileHash -LiteralPath $dllPath -Algorithm SHA256).Hash
$sourceHash = (Get-FileHash -LiteralPath $sourceCpp -Algorithm SHA256).Hash
$stretchHash = (Get-FileHash -LiteralPath $stretchHeader -Algorithm SHA256).Hash
$stftHash = (Get-FileHash -LiteralPath $stftHeader -Algorithm SHA256).Hash

$receipt = [ordered]@{
    schema = 'vwa.chorus_pitch_native.build.v1'
    result = 'built'
    started_utc = $startedUtc
    finished_utc = [DateTime]::UtcNow.ToString('o')
    compiler = [ordered]@{ path = $GxxPath; version = $gxxVersion }
    upstream = [ordered]@{
        signalsmith_stretch_commit = $pinStretchCommit
        signalsmith_stretch_header_sha256 = $stretchHash
        signalsmith_linear_commit = $pinLinearCommit
        signalsmith_linear_stft_sha256 = $stftHash
        license = 'MIT'
    }
    artifacts = [ordered]@{
        dll = [ordered]@{ path = $dllPath; sha256 = $dllHash }
    }
    sources_sha256 = [ordered]@{
        'src/native/vwa_chorus_pitch.cpp' = $sourceHash
    }
}
$receiptPath = Join-Path $OutputDirectory 'build-receipt.json'
Write-Utf8Text $receiptPath (($receipt | ConvertTo-Json -Depth 8) + [Environment]::NewLine)

if ($InstallForDevelopment) {
    $componentDir = Join-Path $repoRoot 'components\native-audio'
    New-Item -ItemType Directory -Force -Path $componentDir | Out-Null
    Copy-Item -LiteralPath $dllPath -Destination (Join-Path $componentDir 'vwa_chorus_pitch.dll') -Force
    $manifest = [ordered]@{
        schema = 'vwa.chorus-pitch.component/v1'
        abi = 1
        dll_sha256 = $dllHash
        signalsmith_stretch_commit = $pinStretchCommit
        signalsmith_linear_commit = $pinLinearCommit
        license = 'MIT'
        sources_sha256 = $receipt.sources_sha256
    }
    Write-Utf8Text (Join-Path $componentDir 'chorus-pitch.json') (($manifest | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
}
Write-Output $receiptPath
