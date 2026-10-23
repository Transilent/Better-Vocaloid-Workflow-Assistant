param(
    [string]$Archive = '',
    [string]$AppRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$Offline,
    [ValidateSet('auto','direct','mirror','gitproxy','gh-proxy','ghfast','hf-mirror','pypi-tuna','pypi-aliyun','pytorch-sjtu')]
    [string]$DownloadSource = 'auto',
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
function Assert-SimpleFileName([string]$Name, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name -in @('.', '..') -or
        [IO.Path]::GetFileName($Name) -cne $Name -or $Name.Contains('\') -or
        $Name.Contains('/') -or $Name.Contains(':')) {
        throw ('{0} must be a single file name: {1}' -f $Label, $Name)
    }
}
function Get-PortableDownload($Metadata) {
    if ($null -eq $Metadata) { return $null }
    $downloads = $Metadata.portable_downloads
    if ($null -ne $downloads) {
        if ($null -eq $downloads.full -or $null -eq $downloads.full.archive -or $null -eq $downloads.full.parts) {
            throw 'portable_downloads.full is missing a complete archive description.'
        }
        return $downloads.full
    }
    if ($null -ne $Metadata.archive -and $null -ne $Metadata.parts) { return $Metadata }
    return $null
}
function Get-PortableReleaseName($Metadata) {
    if ($null -eq $Metadata) { return '' }
    $value = $Metadata.portable_release
    if ($value -is [string]) { return $value }
    if ($null -ne $value) {
        foreach ($name in @('tag', 'release', 'version')) {
            $property = $value.PSObject.Properties[$name]
            if ($null -ne $property -and $property.Value -is [string] -and $property.Value) { return $property.Value }
        }
    }
    return ''
}
function Assert-PortableDownload($Download) {
    if ($null -eq $Download -or $null -eq $Download.archive -or $null -eq $Download.parts) {
        throw 'Portable metadata is incomplete.'
    }
    $parts = @($Download.parts)
    if ($parts.Count -lt 1 -or $parts.Count -gt 999) { throw 'Portable metadata has an invalid part count.' }
    $archiveName = [string]$Download.archive.name
    if (!$archiveName) {
        $firstName = [string]$parts[0].name
        if ($firstName -cmatch '^((VWA|BVWA)-Windows-x64\.zip)\.\d{3}$') { $archiveName = $Matches[1] }
    }
    if ($archiveName -cnotmatch '^(VWA|BVWA)-Windows-x64\.zip$') {
        throw ('Invalid portable archive name: {0}' -f $archiveName)
    }
    $layoutRoot = $Matches[1]
    $declared = @()
    if ($null -ne $Download.layout_root) { $declared += [string]$Download.layout_root }
    if ($null -ne $Download.portable_release -and $Download.portable_release -isnot [string]) {
        if ($null -ne $Download.portable_release.layout_root) { $declared += [string]$Download.portable_release.layout_root }
        if ($null -ne $Download.portable_release.variant -and [string]$Download.portable_release.variant -cne 'full') {
            throw 'The installer requires the full portable variant.'
        }
    }
    foreach ($value in $declared) {
        if ($value -cnotin @('VWA', 'BVWA') -or !$value.Equals($layoutRoot, [StringComparison]::Ordinal)) {
            throw 'Portable metadata layout root does not match its archive name.'
        }
    }
    if ($Download.archive.bytes -isnot [int] -and $Download.archive.bytes -isnot [long]) { throw 'Invalid portable archive size.' }
    if ([long]$Download.archive.bytes -le 0) { throw 'Invalid portable archive size.' }
    $archiveHash = [string]$Download.archive.sha256
    if ($archiveHash -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid portable archive SHA256.' }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $expectedNumber = 1
    $totalBytes = [long]0
    foreach ($part in $parts) {
        if ($null -eq $part -or $null -eq $part.name) { throw 'Each portable part needs a name.' }
        $name = [string]$part.name
        Assert-SimpleFileName $name 'portable part name'
        $pattern = '^' + [regex]::Escape($archiveName) + '\.(\d{3})$'
        if ($name -cnotmatch $pattern) { throw ('Invalid portable part name: {0}' -f $name) }
        if ([int]$Matches[1] -ne $expectedNumber) { throw 'Portable parts must be numbered consecutively from .001.' }
        if (!$seen.Add($name)) { throw ('Duplicate portable part name: {0}' -f $name) }
        if ($part.bytes -isnot [int] -and $part.bytes -isnot [long]) { throw ('Invalid portable part size: {0}' -f $name) }
        if ([long]$part.bytes -le 0) { throw ('Invalid portable part size: {0}' -f $name) }
        if (([string]$part.sha256) -cnotmatch '^[0-9a-fA-F]{64}$') { throw ('Invalid portable part SHA256: {0}' -f $name) }
        $totalBytes += [long]$part.bytes
        $expectedNumber++
    }
    if ($totalBytes -ne [long]$Download.archive.bytes) { throw 'Portable part sizes do not match the archive size.' }
    return [pscustomobject]@{ Name = $archiveName; LayoutRoot = $layoutRoot; Bytes = [long]$Download.archive.bytes; Sha256 = $archiveHash.ToLowerInvariant(); Parts = $parts }
}
function Invoke-DirectRequest([string]$Uri, [string]$OutFile = '') {
    # Windows PowerShell 5.1 compatibility: an empty per-request proxy overrides
    # Internet Settings without changing system settings or process defaults.
    $request = [Net.WebRequest]::CreateHttp([Uri]$Uri)
    $request.Proxy = [Net.WebProxy]::new()
    $request.UserAgent = 'VWA-Runtime-Installer'
    $request.Timeout = 30000
    $request.ReadWriteTimeout = 30000
    $response = $null
    $stream = $null
    $writer = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $stream = $response.GetResponseStream()
        if ($OutFile) {
            $VwaNetworkPending = $OutFile + '.network-partial'
            $writer = [IO.File]::Create((Get-NativePath $VwaNetworkPending))
            $stream.CopyTo($writer)
            $writer.Dispose()
            $writer = $null
            Move-Item -LiteralPath $VwaNetworkPending -Destination $OutFile -Force
        } else {
            $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
            return ($reader.ReadToEnd() | ConvertFrom-Json)
        }
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($writer) { $writer.Dispose() }
        if ($stream) { $stream.Dispose() }
        if ($response) { $response.Dispose() }
    }
}
function Get-DownloadRoutes([string]$Uri) {
    $parsedUri = [Uri]$Uri
    $publicFile = $parsedUri.Scheme -eq 'https' -and $parsedUri.Authority -eq 'github.com' -and
        !$parsedUri.UserInfo -and !$parsedUri.Query -and !$parsedUri.Fragment -and
        ($parsedUri.AbsolutePath -match '/releases/(download/|latest/download/)')
    if (!$publicFile -or $DownloadSource -in @('direct','hf-mirror','pypi-tuna','pypi-aliyun','pytorch-sjtu')) { return @($Uri) }
    $routeMap = [ordered]@{
        'gitproxy' = 'https://api.gitproxy.dev/' + $Uri.Substring(8)
        'gh-proxy' = 'https://gh-proxy.com/' + $Uri
        'ghfast' = 'https://ghfast.top/' + $Uri
    }
    if ($routeMap.Contains($DownloadSource)) { return @($routeMap[$DownloadSource], $Uri) }
    if ($DownloadSource -eq 'mirror') { return @($routeMap.Values) + @($Uri) }
    return @($Uri) + @($routeMap.Values)
}
function Invoke-DirectDownload([string]$Uri, [string]$OutFile = '') {
    # Requests remain direct at the transport level; only public release files
    # can use the explicitly selected accelerator. API/auth URLs stay original.
    $lastDownloadError = $null
    foreach ($downloadRoute in (Get-DownloadRoutes $Uri)) {
        try { return Invoke-DirectRequest $downloadRoute $OutFile }
        catch { $lastDownloadError = $_ }
    }
    throw $lastDownloadError
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
    $expectedArchiveHash = ''
    $expectedLayoutRoot = ''
    if (!$Archive) {
        foreach ($archiveName in @('VWA-Windows-x64.zip', 'BVWA-Windows-x64.zip')) {
            foreach ($folder in @($app, (Split-Path -Parent $app))) {
                $candidate = Join-Path $folder $archiveName
                if (Test-Path -LiteralPath $candidate -PathType Leaf) { $Archive = $candidate; break }
            }
            if ($Archive) { break }
        }
        if (!$Archive) {
            foreach ($archiveName in @('VWA-Windows-x64.zip', 'BVWA-Windows-x64.zip')) {
                $cachedArchive = Join-Path $cache $archiveName
                if (!(Test-Path -LiteralPath $cachedArchive -PathType Leaf) -or
                    !(Test-Path -LiteralPath ($cachedArchive + '.sha256') -PathType Leaf)) { continue }
                $cachedHash = ((Get-Content -LiteralPath ($cachedArchive + '.sha256') -Raw).Trim() -split '\s+')[0]
                if ($cachedHash -cmatch '^[0-9a-fA-F]{64}$' -and (Get-DependencyHash $cachedArchive) -eq $cachedHash.ToLowerInvariant()) {
                    $Archive = $cachedArchive
                    $expectedArchiveHash = $cachedHash.ToLowerInvariant()
                    break
                }
            }
        }
    }
    if (!$Archive) {
        if ($Offline) { throw 'No portable ZIP found. Supply -Archive, or allow the Release download.' }
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $release = Invoke-DirectDownload $ReleaseApiUri
        $metadataAsset = @($release.assets | Where-Object { $_.name -eq 'release-assets.json' })
        if ($metadataAsset.Count -ne 1) { throw 'The Release does not contain a runtime inventory.' }
        $assets = Invoke-DirectDownload $metadataAsset[0].browser_download_url
        $portableDownload = Get-PortableDownload $assets
        if ($null -eq $portableDownload) {
            $portableRelease = Get-PortableReleaseName $assets
            if ($portableRelease -notmatch '^v\d+\.\d+\.\d+$') { throw 'This Release has no portable runtime or compatible base Release.' }
            $baseApi = [Uri]::new([Uri]$ReleaseApiUri, ('tags/' + $portableRelease)).AbsoluteUri
            $release = Invoke-DirectDownload $baseApi
            $metadataAsset = @($release.assets | Where-Object { $_.name -eq 'release-assets.json' })
            if ($metadataAsset.Count -ne 1) { throw 'The base Release does not contain a runtime inventory.' }
            $assets = Invoke-DirectDownload $metadataAsset[0].browser_download_url
            $portableDownload = Get-PortableDownload $assets
            if ($null -eq $portableDownload) { throw 'The base Release has no complete portable runtime.' }
        }
        $portable = Assert-PortableDownload $portableDownload
        $expectedLayoutRoot = $portable.LayoutRoot
        $expectedArchiveHash = $portable.Sha256
        Write-Output ('Downloading portable runtime: approximately {0:N1} GB. This may take several minutes.' -f ($portable.Bytes / 1000000000))
        foreach ($part in @($portable.Parts)) {
            $remote = @($release.assets | Where-Object { $_.name -ceq $part.name })
            if ($remote.Count -ne 1) { throw ('Missing Release part: ' + $part.name) }
            $partPath = Join-Path $cache $part.name
            $valid = (Test-Path -LiteralPath $partPath -PathType Leaf) -and
                ((Get-Item -LiteralPath $partPath).Length -eq [long]$part.bytes)
            if ($valid) { $valid = (Get-DependencyHash $partPath) -eq ([string]$part.sha256).ToLowerInvariant() }
            if (!$valid) {
                Write-Output ('Downloading ' + $part.name)
                Invoke-DirectDownload $remote[0].browser_download_url $partPath
            }
            if ((Get-Item -LiteralPath $partPath).Length -ne [long]$part.bytes -or
                (Get-DependencyHash $partPath) -ne ([string]$part.sha256).ToLowerInvariant()) {
                throw ('Invalid downloaded part: ' + $part.name)
            }
        }
        $Archive = Join-Path $cache $portable.Name
        $VwaDownloadPending = $Archive + '.download-partial'
        $joined = [IO.File]::Create($VwaDownloadPending)
        try {
            foreach ($part in @($portable.Parts)) {
                $partStream = [IO.File]::OpenRead((Join-Path $cache $part.name))
                try { $partStream.CopyTo($joined) } finally { $partStream.Dispose() }
            }
        } finally { $joined.Dispose() }
        if ((Get-DependencyHash $VwaDownloadPending) -ne $expectedArchiveHash) { throw 'Downloaded portable ZIP checksum mismatch.' }
        Move-Item -LiteralPath $VwaDownloadPending -Destination $Archive -Force
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
    $zip = [IO.Compression.ZipFile]::OpenRead((Get-NativePath $Archive))
    $layoutRoots = @{}
    foreach ($member in $zip.Entries) {
        $memberName = [string]$member.FullName
        if ([string]::IsNullOrEmpty($memberName)) { throw 'Portable ZIP contains an empty member name.' }
        $root = ($memberName -split '/', 2)[0]
        if ($root -cnotin @('VWA', 'BVWA')) {
            throw ('Portable ZIP member has an unsupported root: {0}' -f $memberName)
        }
        $layoutRoots[$root] = $true
    }
    if ($layoutRoots.Count -ne 1) { throw 'Portable ZIP must contain exactly one VWA or BVWA runtime root.' }
    $layoutRoot = [string](@($layoutRoots.Keys)[0])
    if ($expectedLayoutRoot -and $layoutRoot -cne $expectedLayoutRoot) {
        throw 'Portable ZIP layout root does not match its metadata.'
    }
    foreach ($member in $zip.Entries) {
        $memberName = [string]$member.FullName
        if ([string]::IsNullOrEmpty($memberName) -or $memberName.Contains('\') -or $memberName.Contains(':') -or
            !$memberName.StartsWith($layoutRoot + '/', [StringComparison]::Ordinal) -or
            @($memberName.TrimEnd('/') -split '/' | Where-Object { $_ -in @('', '.', '..') }).Count) {
            throw ('Portable ZIP member escapes the {0} root: {1}' -f $layoutRoot, $memberName)
        }
    }
    Write-Output 'Installing fixed dependencies; every file is checked against the repository manifest.'
    New-Item -ItemType Directory -Path (Split-Path -Parent $marker) -Force | Out-Null
    [IO.File]::WriteAllText($marker, 'Installation in progress; rerun tools/Start-Diagnostics.bat if interrupted.')
    $count = 0
    $installed = 0
    foreach ($entry in $manifest.entries) {
        $destination = [IO.Path]::GetFullPath((Join-Path $app $entry.file))
        $nativeDestination = Get-NativePath $destination
        $valid = [IO.File]::Exists($nativeDestination) -and ([IO.FileInfo]::new($nativeDestination).Length -eq $entry.bytes)
        if ($valid) { $valid = (Get-DependencyHash $destination) -eq $entry.sha256 }
        if (!$valid) {
            $member = $zip.GetEntry($layoutRoot + '/' + $entry.file)
            if (!$member -or $member.Length -ne $entry.bytes) { throw ('Missing or incompatible runtime file: ' + $entry.file) }
            [IO.Directory]::CreateDirectory((Get-NativePath (Split-Path -Parent $destination))) | Out-Null
            $VwaMemberPending = $destination + '.vwa-partial'
            $memberStream = $member.Open()
            $pendingStream = [IO.File]::Create((Get-NativePath $VwaMemberPending))
            try { $memberStream.CopyTo($pendingStream) } finally { $pendingStream.Dispose(); $memberStream.Dispose() }
            if ((Get-DependencyHash $VwaMemberPending) -ne $entry.sha256) { throw ('Runtime checksum mismatch: ' + $entry.file) }
            if ([IO.File]::Exists($nativeDestination)) { [IO.File]::Delete($nativeDestination) }
            [IO.File]::Move((Get-NativePath $VwaMemberPending), $nativeDestination)
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
