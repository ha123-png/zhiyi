$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$webUrl = "http://localhost:5180"
$apiUrl = "http://127.0.0.1:8010/api/v1/system/status"

function Test-DocumentPipeline {
    try {
        $web = Invoke-WebRequest -Uri $webUrl -UseBasicParsing -TimeoutSec 2
        $api = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 2
        return $web.StatusCode -eq 200 -and $api.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (Test-DocumentPipeline) {
    Write-Host "Document Pipeline is already running. Opening the page..."
    Start-Process $webUrl
    exit 0
}

Set-Location $projectRoot
$env:VITE_API_BASE_URL = "http://127.0.0.1:8010/api/v1"
$env:DOCUMENT_PIPELINE_DATA_DIR = ".local\runtime\data"
$env:DOCUMENT_PIPELINE_QUEUE_DB = ".local\runtime\queue.db"
$env:DOCUMENT_PIPELINE_WORKER_HEARTBEAT = ".local\runtime\worker-heartbeat.json"
$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = $env:NO_PROXY

if (-not $env:DOCUMENT_PIPELINE_MODEL_PROVIDER) {
    $env:DOCUMENT_PIPELINE_MODEL_PROVIDER = "lm_studio"
}
if (-not $env:DOCUMENT_PIPELINE_MODEL_BASE_URL) {
    $env:DOCUMENT_PIPELINE_MODEL_BASE_URL = "http://127.0.0.1:1234/v1"
}
if (-not $env:DOCUMENT_PIPELINE_MODEL_NAME) {
    $env:DOCUMENT_PIPELINE_MODEL_NAME = "qwen3.5-4b"
}

Write-Host ""
Write-Host "Starting Document Pipeline. Keep this window open."
Write-Host "The browser will open automatically when the page is ready."
Write-Host ""

Start-Process powershell.exe `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSScriptRoot\open-when-ready.ps1`"" `
    -WindowStyle Hidden

& npm.cmd run dev:safe
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Startup failed. See the error above."
}

exit $exitCode
