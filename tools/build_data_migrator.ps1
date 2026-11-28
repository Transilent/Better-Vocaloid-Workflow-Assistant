<#
.SYNOPSIS
Compile the standalone zero-dependency VWA Cross-Version Data Migrator (.NET Framework 4.8 WPF).
Requires only built-in Windows csc.exe (present on every Windows 10/11 & GitHub Actions windows-latest).
#>
param(
    [string]$OutputPath = "",
    [string]$Version = ""
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SrcDir = Join-Path $RepoRoot 'packaging\data-migrator'
$IconPath = Join-Path $RepoRoot 'assets\app.ico'
$ManifestPath = Join-Path $SrcDir 'app.manifest'

if (-not $Version) {
    $verJson = Get-Content -LiteralPath (Join-Path $RepoRoot 'packaging\version.json') -Raw | ConvertFrom-Json
    $Version = [string]$verJson.version
}
if (-not $OutputPath) {
    $outDir = Join-Path $RepoRoot 'dist\migrator'
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $OutputPath = Join-Path $outDir "VWA-Data-Migrator-$Version-x64.exe"
} else {
    $parent = Split-Path -Parent $OutputPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
}

$FwDir = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$Csc = Join-Path $FwDir 'csc.exe'
if (-not (Test-Path -LiteralPath $Csc)) {
    throw "Built-in .NET Framework 4.x compiler not found at $Csc"
}

$Sources = @(
    (Join-Path $SrcDir 'I18n.cs'),
    (Join-Path $SrcDir 'WindowsHelpers.cs'),
    (Join-Path $SrcDir 'MigrationEngine.cs'),
    (Join-Path $SrcDir 'MigratorWindow.cs'),
    (Join-Path $SrcDir 'Program.cs')
)

$Refs = @(
    (Join-Path $FwDir 'WPF\PresentationFramework.dll'),
    (Join-Path $FwDir 'WPF\PresentationCore.dll'),
    (Join-Path $FwDir 'WPF\WindowsBase.dll'),
    (Join-Path $FwDir 'System.Xaml.dll'),
    (Join-Path $FwDir 'System.Web.Extensions.dll')
)

$ArgsList = @(
    '/nologo',
    '/target:winexe',
    '/platform:x64',
    '/optimize+',
    '/warn:4',
    "/out:$OutputPath",
    "/win32manifest:$ManifestPath"
)
if (Test-Path -LiteralPath $IconPath) {
    $ArgsList += "/win32icon:$IconPath"
}
foreach ($r in $Refs) {
    $ArgsList += "/reference:$r"
}
$ArgsList += $Sources

& $Csc @ArgsList
if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE"
}

$info = Get-Item -LiteralPath $OutputPath
$shaAlgo = [System.Security.Cryptography.SHA256]::Create()
$stream = [System.IO.File]::OpenRead($info.FullName)
try {
    $sha = ([System.BitConverter]::ToString($shaAlgo.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
} finally {
    $stream.Dispose()
    $shaAlgo.Dispose()
}
Write-Host ("BUILT_DATA_MIGRATOR path={0} bytes={1} sha256={2}" -f $info.FullName, $info.Length, $sha)
