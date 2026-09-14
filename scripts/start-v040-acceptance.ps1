param([switch]$Stop, [switch]$Desktop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'apps/api/.venv/Scripts/python.exe'
$acceptanceData = Join-Path $projectRoot '.local/acceptance-v0.4.0'
$webDist = Join-Path $projectRoot 'apps/web/dist'
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Project Python environment is missing.' }
if ($Stop) {
    & $pythonPath -m document_pipeline_api.launcher stop --data-dir $acceptanceData
    exit $LASTEXITCODE
}
if ($Desktop -and $NoBrowser) { throw '-Desktop and -NoBrowser cannot be combined.' }
if (-not (Test-Path -LiteralPath (Join-Path $webDist 'index.html') -PathType Leaf)) {
    throw 'Build the production frontend first: npm.cmd --prefix apps/web run build'
}
& $pythonPath (Join-Path $PSScriptRoot 'prepare-v040-acceptance.py')
if ($LASTEXITCODE -ne 0) { throw 'Acceptance preparation failed; service was not started.' }
$env:DOCUMENT_PIPELINE_WEB_DIR = $webDist
Write-Host 'Zhiyi v0.4.0: http://127.0.0.1:8814/'
Write-Host 'This console belongs to the acceptance supervisor. Use the stop entry to stop; data will be preserved.'
# The existing supervisor rejects an occupied explicit port. It owns API and
# Worker process lifetimes, health checks and runtime logs.
if ($Desktop) {
    & $pythonPath -c "from pathlib import Path; import sys; from document_pipeline_api.desktop import run_desktop; run_desktop(Path(sys.argv[1]), 8814)" $acceptanceData
} elseif ($NoBrowser) {
    & $pythonPath -m document_pipeline_api.launcher start --data-dir $acceptanceData --port 8814 --no-browser
} else {
    & $pythonPath -m document_pipeline_api.launcher start --data-dir $acceptanceData --port 8814
}
exit $LASTEXITCODE
