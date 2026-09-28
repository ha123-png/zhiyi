param(
    [switch]$Stop,
    [switch]$Browser,
    [switch]$NoBrowser,
    [switch]$SkipBuild,
    [string]$DataDir,
    [ValidateRange(1, 65535)][int]$Port = 8813
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'apps/api/.venv/Scripts/python.exe'
$projectRoot = (& $pythonPath -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' $projectRoot).Trim()
if ($LASTEXITCODE -ne 0) { throw '无法确认项目真实目录。' }
$pythonPath = Join-Path $projectRoot 'apps/api/.venv/Scripts/python.exe'
if (-not $DataDir) { $DataDir = Join-Path $projectRoot '.local/v050-20260927-main/data' }
$acceptanceData = (& $pythonPath -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' $DataDir).Trim()
if ($LASTEXITCODE -ne 0) { throw '无法确认验收数据真实目录。' }
$localRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot '.local')) + [IO.Path]::DirectorySeparatorChar
if (-not $acceptanceData.StartsWith($localRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw '验收数据目录必须位于项目 .local 内。'
}
Set-Location -LiteralPath $projectRoot
if ($Stop) {
    & $pythonPath -m document_pipeline_api.launcher stop --data-dir $acceptanceData
    exit $LASTEXITCODE
}
$statePath = Join-Path $acceptanceData 'runtime/supervisor.json'
$running = $false
if (Test-Path -LiteralPath $statePath) {
    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)"
        $running = $process.CommandLine -like '*document_pipeline_api.launcher*' -and
            $process.CommandLine.Contains($acceptanceData) -and
            (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$($state.app_port)/api/v1/health" -TimeoutSec 3).StatusCode -eq 200
        if ($running) { $Port = [int]$state.app_port }
    } catch { $running = $false }
}
if (-not $running) {
    if (-not $SkipBuild) {
        & npm.cmd --prefix apps/web run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败，未启动。' }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'apps/web/dist/index.html'))) {
        throw '缺少前端构建，请去掉 -SkipBuild 后重试。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $acceptanceData 'document-pipeline.db'))) {
        & $pythonPath scripts/prepare-convergence-acceptance.py --data-dir $acceptanceData
        if ($LASTEXITCODE -ne 0) { throw '合成记录准备失败，未启动。' }
    }
    $env:DOCUMENT_PIPELINE_WEB_DIR = Join-Path $projectRoot 'apps/web/dist'
    $logRoot = Split-Path -Parent $acceptanceData
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $startup = Start-Process -FilePath $pythonPath -ArgumentList @(
        '-m', 'document_pipeline_api.launcher', 'start', '--data-dir', "`"$acceptanceData`"",
        '--port', "$Port", '--no-browser'
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logRoot "start-$stamp.out.log") `
        -RedirectStandardError (Join-Path $logRoot "start-$stamp.err.log")
    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 300
        $startup.Refresh()
        if ($startup.HasExited) { throw "启动失败，请查看 $logRoot 中本次 start 日志。" }
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $supervisor = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)"
            $running = ([int]$state.pid -eq $startup.Id -or $supervisor.ParentProcessId -eq $startup.Id) -and
                (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2).StatusCode -eq 200
        } catch { $running = $false }
    } until ($running -or (Get-Date) -ge $deadline)
    if (-not $running) { throw "启动尚未就绪，请查看 $logRoot 中本次 start 日志。" }
}
$url = "http://127.0.0.1:$Port/"
Write-Host "知意验收版：$url"
Write-Host "数据目录：$acceptanceData"
Write-Host '启动只读取现有数据，不自动调用模型。关闭浏览器不停止服务；停止请使用本脚本 -Stop。'
if (-not $NoBrowser) { Start-Process $url }
