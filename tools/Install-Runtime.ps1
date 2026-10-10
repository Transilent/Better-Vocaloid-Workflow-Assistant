param(
    [string]$Archive = '',
    [string]$AppRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$Offline,
    [string]$ReleaseApiUri = 'https://api.github.com/repos/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Get-NativePath([string]$FilePath) {
    if ($FilePath.StartsWith('\\?\')) { return $FilePath }
    if ($FilePath.StartsWith('\\')) { return '\\?\UNC\' + $FilePath.Substring(2) }
    return '\\?\' + $FilePath
}
function Get-DependencyHash([string]$FilePath) {
    $stream = [IO.File]::OpenRead((Get-NativePath $FilePath))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}
function Get-DisplayPath([string]$FilePath) {
    if ($FilePath.StartsWith('\\?\UNC\')) { return '\\' + $FilePath.Substring(8) }
    if ($FilePath.StartsWith('\\?\')) { return $FilePath.Substring(4) }
    return $FilePath
}
$AppRoot = Get-DisplayPath $AppRoot
$Archive = Get-DisplayPath $Archive
$app = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$cache = Join-Path $app 'cache/runtime-install'
$marker = Join-Path $app 'cache/runtime-install-in-progress'
$zip = $null
try {
    $manifest = Get-Content -LiteralPath (Join-Path $app 'dependencies/manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    # Validate every destination before downloading or modifying any files.
    foreach ($entry in $manifest.entries) {
        if ($entry.file -notmatch '^(dependencies|models|vendor)/' -or $entry.file.Contains(':') -or $entry.file.Contains('\') -or @($entry.file -split '/' | Where-Object { $_ -in @('', '.', '..') }).Count) { throw 'Invalid dependency path.' }
        $destination = [IO.Path]::GetFullPath((Join-Path $app $entry.file))
        if (!$destination.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Dependency path escapes the application folder.' }
        if ($entry.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid dependency checksum.' }
    }
    if (!$Archive) {
        foreach ($candidate in @((Join-Path $app 'BVWA-Windows-x64.zip'), (Join-Path (Split-Path -Parent $app) 'BVWA-Windows-x64.zip'))) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $Archive = $candidate; break }
        }
        $cachedArchive = Join-Path $cache 'BVWA-Windows-x64.zip'
        if (!$Archive -and (Test-Path -LiteralPath $cachedArchive) -and (Test-Path -LiteralPath ($cachedArchive + '.sha256'))) {
            $cachedHash = (Get-Content -LiteralPath ($cachedArchive + '.sha256') -Raw).Trim()
            if ((Get-DependencyHash $cachedArchive) -eq $cachedHash) { $Archive = $cachedArchive }
        }
    }
    $expectedArchiveHash = ''
    if (!$Archive) {
        if ($Offline) { throw 'No portable ZIP found. Supply -Archive, or allow the Release download.' }
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $release = Invoke-RestMethod -Uri $ReleaseApiUri -Headers @{'User-Agent'='BVWA-Runtime-Installer'}
        $metadataAsset = @($release.assets | Where-Object { $_.name -eq 'release-assets.json' })
        if ($metadataAsset.Count -ne 1) { throw 'The Release does not contain a runtime inventory.' }
        $assets = Invoke-RestMethod -Uri $metadataAsset[0].browser_download_url
        Write-Output ('Downloading portable runtime: approximately {0:N1} GB. This may take several minutes.' -f ($assets.archive.bytes / 1000000000))
        foreach ($part in $assets.parts) {
            if ([IO.Path]::GetFileName($part.name) -ne $part.name -or $part.name -notmatch '^BVWA-Windows-x64\.zip\.\d{3}$') { throw 'Invalid Release part name.' }
            $remote = @($release.assets | Where-Object { $_.name -eq $part.name })
            if ($remote.Count -ne 1) { throw ('Missing Release part: ' + $part.name) }
            $partPath = Join-Path $cache $part.name
            $valid = (Test-Path -LiteralPath $partPath) -and ((Get-Item -LiteralPath $partPath).Length -eq $part.bytes)
            if ($valid) { $valid = (Get-DependencyHash $partPath) -eq $part.sha256 }
            if (!$valid) {
                Write-Output ('Downloading ' + $part.name)
                Invoke-WebRequest -Uri $remote[0].browser_download_url -UseBasicParsing -OutFile $partPath
            }
            if ((Get-Item -LiteralPath $partPath).Length -ne $part.bytes -or (Get-DependencyHash $partPath) -ne $part.sha256) { throw ('Invalid downloaded part: ' + $part.name) }
        }
        $Archive = Join-Path $cache 'BVWA-Windows-x64.zip'
        $downloadPending = $Archive + '.download-partial'
        $joined = [IO.File]::Create($downloadPending)
        try {
            foreach ($part in $assets.parts) {
                $partStream = [IO.File]::OpenRead((Join-Path $cache $part.name))
                try { $partStream.CopyTo($joined) } finally { $partStream.Dispose() }
            }
        } finally { $joined.Dispose() }
        $expectedArchiveHash = $assets.archive.sha256
        if ((Get-DependencyHash $downloadPending) -ne $expectedArchiveHash) { throw 'Downloaded portable ZIP checksum mismatch.' }
        Move-Item -LiteralPath $downloadPending -Destination $Archive -Force
        [IO.File]::WriteAllText($Archive + '.sha256', $expectedArchiveHash)
    }
    $Archive = (Resolve-Path -LiteralPath $Archive).Path
    if (!$expectedArchiveHash -and (Test-Path -LiteralPath ($Archive + '.sha256'))) {
        $expectedArchiveHash = ((Get-Content -LiteralPath ($Archive + '.sha256') -Raw).Trim() -split '\s+')[0]
    }
    if ($expectedArchiveHash) {
        Write-Output 'Checking portable ZIP checksum...'
        if ((Get-DependencyHash $Archive) -ne $expectedArchiveHash) { throw 'Portable ZIP checksum mismatch.' }
    }
    Write-Output 'Installing fixed dependencies; every file is checked against the repository manifest.'
    New-Item -ItemType Directory -Path (Split-Path -Parent $marker) -Force | Out-Null
    [IO.File]::WriteAllText($marker, 'Installation in progress; rerun tools/Start-Diagnostics.bat if interrupted.')
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    $count = 0
    $installed = 0
    foreach ($entry in $manifest.entries) {
        $destination = [IO.Path]::GetFullPath((Join-Path $app $entry.file))
        $nativeDestination = Get-NativePath $destination
        $valid = [IO.File]::Exists($nativeDestination) -and ([IO.FileInfo]::new($nativeDestination).Length -eq $entry.bytes)
        if ($valid) { $valid = (Get-DependencyHash $destination) -eq $entry.sha256 }
        if (!$valid) {
            $member = $zip.GetEntry('BVWA/' + $entry.file)
            if (!$member -or $member.Length -ne $entry.bytes) { throw ('Missing or incompatible runtime file: ' + $entry.file) }
            [IO.Directory]::CreateDirectory((Get-NativePath (Split-Path -Parent $destination))) | Out-Null
            $pending = $destination + '.bvwa-partial'
            $memberStream = $member.Open()
            $pendingStream = [IO.File]::Create((Get-NativePath $pending))
            try { $memberStream.CopyTo($pendingStream) } finally { $pendingStream.Dispose(); $memberStream.Dispose() }
            if ((Get-DependencyHash $pending) -ne $entry.sha256) { throw ('Runtime checksum mismatch: ' + $entry.file) }
            if ([IO.File]::Exists($nativeDestination)) { [IO.File]::Delete($nativeDestination) }
            [IO.File]::Move((Get-NativePath $pending), $nativeDestination)
            $installed++
        }
        $count++
        if ($count % 1000 -eq 0) { Write-Output ('Checked {0}/{1} runtime files.' -f $count, $manifest.entries.Count) }
    }
    Remove-Item -LiteralPath $marker
    Write-Output ('Runtime ready: {0} files verified, {1} installed.' -f $count, $installed)
    exit 0
} catch {
    Write-Output ('Runtime installation failed: ' + $_.Exception.Message)
    Write-Output 'Get the full portable package from https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest'
    Write-Output 'Alternatively, run this script with -Archive followed by the path to the complete ZIP.'
    exit 1
} finally {
    if ($zip) { $zip.Dispose() }
}
