param(
    [Parameter(Mandatory=$true)][string]$BootstrapPython,
    [Parameter(Mandatory=$true)][string]$WorkerPython,
    [Parameter(Mandatory=$true)][string]$ControlDirectory,
    [string]$ApplicationRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RuntimeDirectory = ''
)
$ErrorActionPreference = 'Stop'
$app = (Resolve-Path -LiteralPath $ApplicationRoot).Path
$bootstrap = (Resolve-Path -LiteralPath $BootstrapPython).Path
$worker = (Resolve-Path -LiteralPath $WorkerPython).Path
$control = [IO.Path]::GetFullPath($ControlDirectory).TrimEnd('\')
if ($control.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase) -or $control -eq $app) {
    throw 'Use a separate control directory outside the application installation.'
}
foreach ($path in @($control, $app, $bootstrap, $worker)) {
    $current = $path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Links and reparse points are not supported.'
            }
        }
        $current = Split-Path -Parent $current
    }
}
if (!(Test-Path -LiteralPath (Join-Path $app 'agent_mcp.py'))) {
    throw 'Prepare the source layout first (tools/Prepare-Source.ps1).'
}
New-Item -ItemType Directory -Path $control -Force | Out-Null
foreach ($name in @('exchange','jobs','state','temp')) {
    New-Item -ItemType Directory -Path (Join-Path $control $name) -Force | Out-Null
}
$runtime = if ($RuntimeDirectory) { [IO.Path]::GetFullPath($RuntimeDirectory) } else { Join-Path $control 'runtime' }
if (Test-Path -LiteralPath $runtime) {
    if ((Get-Item -LiteralPath $runtime -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Runtime directory cannot be a reparse point.'
    }
}
$env:TEMP = Join-Path $control 'temp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$sdkPython = Join-Path $runtime 'Scripts/python.exe'
if (!(Test-Path -LiteralPath $sdkPython)) {
    & $bootstrap -m venv $runtime
    if ($LASTEXITCODE) { throw 'Dedicated MCP virtual environment creation failed.' }
}
& $sdkPython -m pip install --disable-pip-version-check --require-hashes -r (Join-Path $app 'packaging/requirements-mcp.lock.txt')
if ($LASTEXITCODE) { throw 'Pinned MCP dependencies could not be installed.' }
& $sdkPython -m pip check
if ($LASTEXITCODE) { throw 'Dedicated MCP dependency check failed.' }
$configuration = Join-Path $control 'config.json'
if (!(Test-Path -LiteralPath $configuration)) {
    [ordered]@{
        schema=1; application_root=$app; worker_python=$worker
        state_root=(Join-Path $control 'state')
        allowed_asset_roots=@((Join-Path $control 'exchange'))
        allowed_job_roots=@((Join-Path $control 'jobs'))
        shared_draft_root=(Join-Path $app 'cache/style-region-drafts')
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configuration -Encoding UTF8
}
[ordered]@{ mcpServers = [ordered]@{ vwa = [ordered]@{
    command=$sdkPython; args=@('-B','-u',(Join-Path $app 'agent_mcp.py'),'--config',$configuration)
    env=[ordered]@{PYTHONUTF8='1';PYTHONDONTWRITEBYTECODE='1'}
}}} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $control 'client-config.json') -Encoding UTF8
$launcher = '@echo off' + "`r`n" + '"' + $sdkPython + '" -B -u "' + (Join-Path $app 'agent_mcp.py') + '" --config "' + $configuration + '"' + "`r`n"
[IO.File]::WriteAllText((Join-Path $control 'Start-VWA-MCP.cmd'), $launcher, [Text.UTF8Encoding]::new($false))
Write-Host ('MCP client configuration: ' + (Join-Path $control 'client-config.json'))
Write-Host ('Review granted directories in: ' + $configuration)
Write-Host 'This setup does not register the server in a host or change the VWA runtime.'
