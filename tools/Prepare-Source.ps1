param([string]$AppRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$app = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$catalog = Join-Path $app 'packaging/source-files.json'
$mapping = Get-Content -LiteralPath $catalog -Raw -Encoding UTF8 | ConvertFrom-Json
$files = $mapping.files
if (($files | Select-Object -Unique).Count -ne $files.Count) { throw 'Source mapping contains duplicate paths.' }
$sourceDirectory = Join-Path $app 'src'
$cataloguedSources = @($files | Where-Object { $_ -match '^src/[A-Za-z0-9_]+\.py$' })
$discoveredSources = @(Get-ChildItem -LiteralPath $sourceDirectory -File -Filter '*.py' |
    ForEach-Object { 'src/' + $_.Name })
$unregistered = @($discoveredSources | Where-Object { $cataloguedSources -notcontains $_ })
$missing = @($cataloguedSources | Where-Object { $discoveredSources -notcontains $_ })
if ($unregistered.Count -or $missing.Count) {
    $details = @()
    if ($unregistered.Count) { $details += 'unregistered: ' + ($unregistered -join ', ') }
    if ($missing.Count) { $details += 'missing: ' + ($missing -join ', ') }
    throw ('Source mapping is incomplete (' + ($details -join '; ') + ').')
}
$copies = @()
$cataloguedQml = @($files | Where-Object { $_ -match '^src/quick_ui/[A-Za-z0-9_]+\.qml$' })
$qmlDirectory = Join-Path $sourceDirectory 'quick_ui'
$discoveredQml = @(if (Test-Path -LiteralPath $qmlDirectory -PathType Container) {
    Get-ChildItem -LiteralPath $qmlDirectory -File -Filter '*.qml' |
        ForEach-Object { 'src/quick_ui/' + $_.Name }
})
if (@($discoveredQml | Where-Object { $cataloguedQml -notcontains $_ }).Count -or
    @($cataloguedQml | Where-Object { $discoveredQml -notcontains $_ }).Count) {
    throw 'Quick resource mapping is incomplete.'
}
foreach ($name in $cataloguedQml) {
    $copies += @{ Source = $name; Destination = $name.Substring(4) }
}
foreach ($name in $files) {
    if ($name -notmatch '^src/[A-Za-z0-9_]+\.py$') { continue }
    $copies += @{ Source = $name; Destination = [IO.Path]::GetFileName($name) }
}
foreach ($name in @('source-files.json', 'portable-files.json', 'config.example.json',
                    'publish-selectors.json', 'version.json', 'requirements-runtime.lock.txt')) {
    $copies += @{ Source = ('packaging/' + $name); Destination = $name }
}
if (!(Test-Path -LiteralPath (Join-Path $app 'config.json'))) {
    $copies += @{ Source = 'packaging/config.example.json'; Destination = 'config.json' }
}
# Validate the complete copy plan before preparing any generated files.
foreach ($copy in $copies) {
    $copy.Source = (Resolve-Path -LiteralPath (Join-Path $app $copy.Source)).Path
    $copy.Destination = [IO.Path]::GetFullPath((Join-Path $app $copy.Destination))
    foreach ($path in @($copy.Source, $copy.Destination)) {
        if (!$path.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Source preparation path escapes the checkout.'
        }
    }
    if (!(Test-Path -LiteralPath $copy.Source -PathType Leaf)) { throw 'Source file is missing.' }
}
foreach ($name in $mapping.retired_generated) {
    if ($name -notmatch '^[A-Za-z0-9_]+\.py$' -or $files -contains ('src/' + $name)) {
        throw 'Invalid retired generated path.'
    }
    $old = Join-Path $app $name
    if (Test-Path -LiteralPath $old -PathType Leaf) {
        if ((Get-Item -LiteralPath $old).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing to remove a linked generated file.'
        }
        Remove-Item -LiteralPath $old
    }
}
foreach ($copy in $copies) {
    $parent = Split-Path -Parent $copy.Destination
    if (!(Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -Path $parent -ItemType Directory | Out-Null
    }
    Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination -Force
}
Write-Host ('Prepared {0} development files. Edit application code in src/.' -f $copies.Count)
