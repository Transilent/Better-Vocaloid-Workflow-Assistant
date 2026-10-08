param(
    [string]$FirstPart = (Join-Path $PSScriptRoot 'BVWA-Windows-x64.zip.001'),
    [string]$Output = (Join-Path $PSScriptRoot 'BVWA-Windows-x64.zip')
)
$ErrorActionPreference = 'Stop'
$resolvedPart = (Resolve-Path -LiteralPath $FirstPart).Path
if ($resolvedPart -notmatch '\.001$') { throw 'FirstPart must end in .001' }
$base = $resolvedPart.Substring(0, $resolvedPart.Length - 3)
$outputPath = [IO.Path]::GetFullPath($Output)
$inventoryPath = Join-Path (Split-Path -Parent $resolvedPart) 'release-assets.json'
$inventory = Get-Content -LiteralPath $inventoryPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($part in $inventory.parts) {
    $partPath = Join-Path (Split-Path -Parent $resolvedPart) $part.name
    if (!(Test-Path -LiteralPath $partPath)) { throw ('Missing part: ' + $part.name) }
    if ((Get-Item -LiteralPath $partPath).Length -ne $part.bytes) { throw ('Wrong size: ' + $part.name) }
    if ((Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $part.sha256) { throw ('Wrong SHA256: ' + $part.name) }
}
if (Test-Path -LiteralPath $outputPath) { throw 'Output already exists; choose another output filename.' }
$stream = [IO.File]::Open($outputPath, [IO.FileMode]::CreateNew)
try {
    foreach ($part in $inventory.parts) {
        $inputStream = [IO.File]::OpenRead((Join-Path (Split-Path -Parent $resolvedPart) $part.name))
        try { $inputStream.CopyTo($stream) } finally { $inputStream.Dispose() }
    }
} finally { $stream.Dispose() }
if ((Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $inventory.archive.sha256) {
    throw 'Merged ZIP checksum mismatch.'
}
Write-Output ('ZIP ready: ' + $outputPath)
