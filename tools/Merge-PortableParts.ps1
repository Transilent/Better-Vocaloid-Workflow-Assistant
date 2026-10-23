param(
    [string]$FirstPart = (Join-Path $PSScriptRoot 'VWA-Windows-x64.zip.001'),
    [string]$Output = '',
    [string]$MetadataFile = ''
)
$ErrorActionPreference = 'Stop'

function Assert-RegularFile([string]$Path, [string]$Label) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { throw ('Missing {0}: {1}' -f $Label, $Path) }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ('{0} is a link or reparse point: {1}' -f $Label, $Path)
    }
    return $item
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        } finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

function Assert-SimpleFileName([string]$Name, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name -in @('.', '..') -or [IO.Path]::GetFileName($Name) -cne $Name -or
        $Name.Contains('\') -or $Name.Contains('/') -or $Name.Contains(':')) {
        throw ('{0} must be a single file name: {1}' -f $Label, $Name)
    }
}

function Read-ReleaseMetadata([string]$Name) {
    Assert-SimpleFileName $Name 'MetadataFile'
    $path = Join-Path $releaseDir $Name
    $null = Assert-RegularFile $path ('metadata ' + $Name)
    return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Test-MetadataForPart($Metadata, [string]$ArchiveName) {
    if ($null -eq $Metadata -or $null -eq $Metadata.archive -or $null -eq $Metadata.parts) { return $false }
    $name = [string]$Metadata.archive.name
    Assert-SimpleFileName $name 'archive.name'
    if (![StringComparer]::OrdinalIgnoreCase.Equals($name, $ArchiveName)) { return $false }
    if ($null -eq $Metadata.parts -or $Metadata.parts.Count -lt 1) { return $false }
    $firstName = [string]$Metadata.parts[0].name
    if ([string]::IsNullOrEmpty($firstName)) { return $false }
    $expected = [IO.Path]::GetFullPath((Join-Path $releaseDir ($name + '.001')))
    return [StringComparer]::OrdinalIgnoreCase.Equals($resolvedPart, $expected)
}

function Assert-ArchiveLayout($Metadata, [string]$ArchiveName) {
    $root = ''
    if ($ArchiveName -cmatch '^(VWA|BVWA)-Windows-x64(?:-JPN)?\.zip$') { $root = $Matches[1] }
    if (!$root) { throw ('Invalid portable archive name: {0}' -f $ArchiveName) }
    $declared = @()
    if ($null -ne $Metadata.layout_root) { $declared += [string]$Metadata.layout_root }
    $release = $Metadata.portable_release
    if ($null -ne $release -and $release -isnot [string] -and $null -ne $release.layout_root) {
        $declared += [string]$release.layout_root
    }
    foreach ($value in $declared) {
        if ($value -cnotin @('VWA', 'BVWA') -or !$value.Equals($root, [StringComparison]::Ordinal)) {
            throw ('Portable metadata layout root does not match {0}' -f $ArchiveName)
        }
    }
}

$firstItem = Assert-RegularFile $FirstPart 'FirstPart'
if ($firstItem.DirectoryName -eq $null) { throw 'FirstPart must be a file.' }
$resolvedPart = [IO.Path]::GetFullPath($firstItem.FullName)
if ($resolvedPart -notmatch '\.001$') { throw 'FirstPart must end in .001' }
$releaseDir = [IO.Path]::GetFullPath($firstItem.DirectoryName)
$releaseItem = Get-Item -LiteralPath $releaseDir -Force
if (($releaseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'Release folder must not be a link or reparse point.'
}
$archiveName = [IO.Path]::GetFileName($resolvedPart.Substring(0, $resolvedPart.Length - 4))

$inventory = $null
$metadataPath = ''
if ($MetadataFile) {
    $inventory = Read-ReleaseMetadata $MetadataFile
    if (!(Test-MetadataForPart $inventory $archiveName)) {
        throw ('MetadataFile does not describe FirstPart: {0}' -f $MetadataFile)
    }
    $metadataPath = Join-Path $releaseDir $MetadataFile
} else {
    $canonicalName = 'portable-metadata-full.json'
    $canonicalPath = Join-Path $releaseDir $canonicalName
    if (Test-Path -LiteralPath $canonicalPath -PathType Leaf) {
        $candidate = Read-ReleaseMetadata $canonicalName
        if (Test-MetadataForPart $candidate $archiveName) {
            $inventory = $candidate
            $metadataPath = $canonicalPath
        }
    }
    if ($null -eq $inventory) {
        $inventory = Read-ReleaseMetadata 'release-assets.json'
        if (!(Test-MetadataForPart $inventory $archiveName)) {
            throw 'FirstPart does not match release-assets.json or portable-metadata-full.json.'
        }
        $metadataPath = Join-Path $releaseDir 'release-assets.json'
    }
}
Assert-ArchiveLayout $inventory $archiveName
$null = Assert-RegularFile $metadataPath 'release metadata'

$expectedBase = [IO.Path]::GetFullPath((Join-Path $releaseDir $archiveName))
$expectedFirst = $expectedBase + '.001'
if (![StringComparer]::OrdinalIgnoreCase.Equals($resolvedPart, $expectedFirst)) {
    throw ('FirstPart does not match archive.name: {0}' -f $archiveName)
}

$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$partPaths = [Collections.Generic.List[string]]::new()
$expectedNumber = 1
$partBytes = [long]0
foreach ($part in $inventory.parts) {
    if ($null -eq $part -or $null -eq $part.name) { throw 'Each part needs a name.' }
    $name = [string]$part.name
    Assert-SimpleFileName $name ('parts[{0}].name' -f ($expectedNumber - 1))
    if ($name -cnotmatch ('^' + [regex]::Escape($archiveName) + '\.(\d{3})$')) {
        throw ('Part name does not belong to {0}: {1}' -f $archiveName, $name)
    }
    $number = [int]$Matches[1]
    if ($number -ne $expectedNumber) { throw ('Parts must be numbered consecutively from .001: {0}' -f $name) }
    if (!$seen.Add($name)) { throw ('Duplicate part name: {0}' -f $name) }
    if ($part.bytes -isnot [int] -and $part.bytes -isnot [long]) { throw ('Invalid part size: {0}' -f $name) }
    if ([long]$part.bytes -le 0) { throw ('Invalid part size: {0}' -f $name) }
    $sha = [string]$part.sha256
    if ($sha -notmatch '^[0-9a-fA-F]{64}$') { throw ('Invalid part SHA256: {0}' -f $name) }
    $partPath = [IO.Path]::GetFullPath((Join-Path $releaseDir $name))
    if (![StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetDirectoryName($partPath), $releaseDir)) {
        throw ('Part escapes the release folder: {0}' -f $name)
    }
    $partItem = Assert-RegularFile $partPath ('part ' + $name)
    if ($partItem.Length -ne [long]$part.bytes) { throw ('Wrong size: {0}' -f $name) }
    if ((Get-Sha256 $partPath) -ne $sha.ToLowerInvariant()) {
        throw ('Wrong SHA256: {0}' -f $name)
    }
    $partPaths.Add($partPath)
    $partBytes += [long]$part.bytes
    $expectedNumber++
}

if ($inventory.archive.bytes -isnot [int] -and $inventory.archive.bytes -isnot [long]) { throw 'Invalid archive size.' }
if ([long]$inventory.archive.bytes -le 0) { throw 'Invalid archive size.' }
if ($partBytes -ne [long]$inventory.archive.bytes) { throw 'Part sizes do not match archive size.' }
$archiveSha = [string]$inventory.archive.sha256
if ($archiveSha -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid archive SHA256.' }

if (!$Output) { $Output = $expectedBase }
$outputPath = [IO.Path]::GetFullPath($Output)
if ((Test-Path -LiteralPath $outputPath) -or
    [StringComparer]::OrdinalIgnoreCase.Equals($outputPath, $metadataPath) -or
    $partPaths.Contains($outputPath)) {
    throw 'Output already exists or overlaps a release input; choose another output filename.'
}

$stream = $null
$temporaryPath = Join-Path ([IO.Path]::GetDirectoryName($outputPath)) ('.vwa-merge-' + [Guid]::NewGuid().ToString('N') + '.tmp')
$ownsTemporary = $false
try {
    $stream = [IO.File]::Open($temporaryPath, [IO.FileMode]::CreateNew)
    $ownsTemporary = $true
    foreach ($partPath in $partPaths) {
        $inputStream = [IO.File]::OpenRead($partPath)
        try { $inputStream.CopyTo($stream) } finally { $inputStream.Dispose() }
    }
    $stream.Dispose()
    $stream = $null
    if ((Get-Item -LiteralPath $temporaryPath).Length -ne [long]$inventory.archive.bytes) {
        throw 'Merged ZIP size mismatch.'
    }
    if ((Get-Sha256 $temporaryPath) -ne $archiveSha.ToLowerInvariant()) {
        throw 'Merged ZIP checksum mismatch.'
    }
    # Move refuses an existing destination, including one created during merge.
    [IO.File]::Move($temporaryPath, $outputPath)
    $ownsTemporary = $false
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
    if ($ownsTemporary -and (Test-Path -LiteralPath $temporaryPath)) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}
Write-Output ('ZIP ready: ' + $outputPath)
