param([string]$AppRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$app = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$catalog = Join-Path $app 'packaging/source-files.json'
$files = (Get-Content -LiteralPath $catalog -Raw -Encoding UTF8 | ConvertFrom-Json).files
$copies = @()
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
foreach ($copy in $copies) {
    Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination -Force
}
Write-Host ('Prepared {0} development files. Edit application code in src/.' -f $copies.Count)
