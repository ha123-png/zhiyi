param([switch]$Stop, [switch]$Browser, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'apps/api/.venv/Scripts/python.exe'
$acceptanceData = Join-Path $projectRoot '.local/acceptance-design-live'
Set-Location -LiteralPath $projectRoot
if ($Stop) {
    & $pythonPath -m document_pipeline_api.launcher stop --data-dir $acceptanceData
    exit $LASTEXITCODE
}
& npm.cmd --prefix apps/web run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败，未启动。' }
if (-not (Test-Path -LiteralPath (Join-Path $acceptanceData 'document-pipeline.db'))) {
    & $pythonPath scripts/prepare-source-acceptance.py --data-dir $acceptanceData --files-dir (Join-Path $projectRoot '.local/acceptance-design-live-files')
    if ($LASTEXITCODE -ne 0) { throw '合成记录准备失败，未启动。' }
}
$env:DOCUMENT_PIPELINE_WEB_DIR = Join-Path $projectRoot 'apps/web/dist'
Write-Host '独立返工验收：8813；已有记录可直接阅读，不会因启动而调用模型。真实 AI 记录与模拟记录分别标注。'
if ($NoBrowser) {
    & $pythonPath -m document_pipeline_api.launcher start --data-dir $acceptanceData --port 8813 --no-browser
} elseif ($Browser) {
    & $pythonPath -m document_pipeline_api.launcher start --data-dir $acceptanceData --port 8813
} else {
    & $pythonPath -c "from pathlib import Path; import sys; from document_pipeline_api.desktop import run_desktop; run_desktop(Path(sys.argv[1]), 8813, window_title='知意 · 设计收敛验收')" $acceptanceData
}
exit $LASTEXITCODE
