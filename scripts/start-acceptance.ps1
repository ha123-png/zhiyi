param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [switch]$Desktop,
    [string]$KeyFile
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'apps\api\.venv\Scripts\python.exe'
$acceptanceData = Join-Path $projectRoot '.local\acceptance-user'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw '开发环境尚未安装 API 依赖，请先完成开发环境安装。'
}
Set-Location -LiteralPath $projectRoot
if ($Stop) {
    & $pythonPath -m document_pipeline_api.launcher stop --data-dir $acceptanceData
    exit $LASTEXITCODE
}
& npm.cmd --prefix apps/web run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败，未启动验收实例。' }
$env:DOCUMENT_PIPELINE_WEB_DIR = Join-Path $projectRoot 'apps\web\dist'
if (-not $KeyFile) { throw '真实云模型验收需要显式传入已授权的 -KeyFile；离线验收请用 npm run test:browser。' }
& $pythonPath scripts/prepare-acceptance.py --key-file $KeyFile
if ($LASTEXITCODE -ne 0) { throw '验收环境准备失败，未启动。' }
Write-Host "隔离验收数据：$acceptanceData"
Write-Host '首次准备会配置测试云模型，密钥按应用机制保存在系统凭据中。只使用合成测试文件。'
Write-Host '此实例使用独立 API 和 Worker，不会加载正式安装的数据。'
if ($Desktop) {
    & $pythonPath -c "from pathlib import Path; import sys; from document_pipeline_api.desktop import run_desktop; run_desktop(Path(sys.argv[1]), 8811, window_title='知意 · 升级验收')" $acceptanceData
    exit $LASTEXITCODE
}
$launchArgs = @('-m', 'document_pipeline_api.launcher', 'start', '--data-dir', $acceptanceData, '--port', '8811')
if ($NoBrowser) { $launchArgs += '--no-browser' }
& $pythonPath @launchArgs
exit $LASTEXITCODE
