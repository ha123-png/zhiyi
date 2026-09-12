param(
    [string[]]$Tests = @('apps/api/tests')
)

$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$testParent = Join-Path $workspacePath '.local/tests'
New-Item -ItemType Directory -Force -Path $testParent | Out-Null
$runDirectory = Join-Path $testParent ('api-' + [guid]::NewGuid().ToString('N'))
Push-Location $workspacePath
try {
    # A fresh workspace-local path avoids machine TEMP permissions and never
    # removes a previous run's evidence through pytest's --basetemp cleanup.
    & uv --cache-dir .uv-cache run --project apps/api pytest @Tests --basetemp=$runDirectory
    $testExitCode = $LASTEXITCODE
    Write-Output "Test artifacts: $runDirectory"
    exit $testExitCode
} finally {
    Pop-Location
}
