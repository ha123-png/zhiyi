param(
    [string]$Version = "",
    [int]$Port = 18767
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Read the version from the root VERSION by default (same gate source as
# build-release.ps1), so smoke artifacts do not drift from the real version
# after an upgrade and the "all passed" result stays honest.
# NOTE: keep comments ASCII-only; PowerShell 5.1 reads .ps1 as ANSI unless a
# BOM is present, and UTF-8 Chinese comments corrupt parsing of nearby lines.
if (-not $Version) {
    $versionFile = Join-Path $projectRoot "VERSION"
    if (-not (Test-Path -LiteralPath $versionFile)) {
        throw "VERSION file is missing at $versionFile; pass -Version explicitly."
    }
    $Version = (Get-Content -LiteralPath $versionFile -Raw).Trim()
}

$distRoot = Join-Path $projectRoot ".local\release\dist"
$compiler = Join-Path $projectRoot ".local\tools\nsis\makensis.exe"
if (-not (Test-Path -LiteralPath $compiler)) {
    $command = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($command) { $compiler = $command.Source }
}
if (-not (Test-Path -LiteralPath $compiler)) {
    $systemCompiler = Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"
    if (Test-Path -LiteralPath $systemCompiler) { $compiler = $systemCompiler }
}
$installerOutput = Join-Path $distRoot "installer"
$testRoot = Join-Path $projectRoot ".local\release-lifecycle"
$installRoot = Join-Path $testRoot "app"
$dataRoot = Join-Path $testRoot "data"
$installer = Join-Path $installerOutput "Zhiyi-$Version-win-x64-smoke-setup.exe"
$marker = Join-Path $dataRoot "preserve-me.txt"
$supervisor = $null
$otherSupervisor = $null
$otherDataRoot = Join-Path $testRoot "other-data"

if (-not $testRoot.StartsWith((Join-Path $projectRoot ".local\release-lifecycle"))) {
    throw "Refusing to use a lifecycle directory outside the workspace."
}
if (-not (Test-Path -LiteralPath $compiler)) { throw "NSIS compiler is missing." }
if (-not (Test-Path -LiteralPath (Join-Path $distRoot "Zhiyi\Zhiyi.exe"))) {
    throw "Build the frozen release before running the lifecycle smoke test."
}

if (Test-Path -LiteralPath $testRoot) {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $installerOutput -Force | Out-Null
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null

& $compiler /INPUTCHARSET UTF8 "/DAPP_VERSION=$Version" "/DBUILD_ROOT=$distRoot" "/DOUTPUT_DIR=$installerOutput" /DSMOKE_MODE "/DSMOKE_DATA_DIR=$dataRoot" (Join-Path $projectRoot "packaging\installer.nsi")
if ($LASTEXITCODE -ne 0) { throw "Smoke installer build failed." }

function Install-SmokePackage {
    $process = Start-Process -FilePath $installer -ArgumentList "/S", "/D=$installRoot" -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Silent install failed: $($process.ExitCode)" }
    if (-not (Test-Path -LiteralPath (Join-Path $installRoot "Zhiyi.exe"))) {
        throw "Installed executable is missing."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installRoot "ZhiyiCLI.exe"))) {
        throw "Installed diagnostic executable is missing."
    }
}

function Start-IsolatedApplication {
    $executable = Join-Path $installRoot "Zhiyi.exe"
    $script:supervisor = Start-Process -FilePath $executable -ArgumentList "--data-dir", $dataRoot, "--port", "$Port", "--no-browser" -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        if ($script:supervisor.HasExited) { throw "Installed application exited during startup." }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 1
            $statePath = Join-Path $dataRoot "runtime\supervisor.json"
            if ($health.status -eq "ok" -and (Test-Path -LiteralPath $statePath)) { return }
        } catch {
            # Keep polling until the bounded deadline.
        }
    } while ((Get-Date) -lt $deadline)
    throw "Installed application did not become healthy within 15 seconds."
}

try {
    # Run the build output as a separate installation. Upgrade/uninstall must
    # not kill it merely because it shares the Zhiyi.exe filename.
    $otherExecutable = Join-Path $distRoot "Zhiyi\Zhiyi.exe"
    $otherPort = $Port + 1
    $otherSupervisor = Start-Process -FilePath $otherExecutable -ArgumentList "--data-dir", $otherDataRoot, "--port", "$otherPort", "--no-browser" -WindowStyle Hidden -PassThru
    $otherDeadline = (Get-Date).AddSeconds(20)
    $otherReady = $false
    do {
        Start-Sleep -Milliseconds 250
        try { $otherReady = (Invoke-RestMethod -Uri "http://127.0.0.1:$otherPort/api/v1/health" -TimeoutSec 1).status -eq "ok" } catch {}
    } while (-not $otherReady -and (Get-Date) -lt $otherDeadline)
    if (-not $otherReady) { throw "Separate installation did not become healthy." }

    Install-SmokePackage
    Start-IsolatedApplication
    Set-Content -LiteralPath $marker -Encoding ascii -Value "business-data-must-survive"
    $markerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $marker).Hash

    # Installing the same release again exercises the safe-stop and overwrite path.
    Install-SmokePackage
    $supervisor.WaitForExit(10 * 1000) | Out-Null
    if (-not $supervisor.HasExited) { throw "Upgrade did not stop the previous supervisor." }

    Start-IsolatedApplication
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $marker).Hash -ne $markerHash) {
        throw "Business data changed during upgrade."
    }

    # Exercise the packaged supervisor restore path, not the source-tree API.
    $backup = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/v1/backups" -TimeoutSec 15
    $restore = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/v1/backups/$($backup.name)/restore" -TimeoutSec 15
    if (-not $restore.scheduled -or -not $restore.restart_required) {
        throw "Packaged restore was not scheduled through the supervisor."
    }
    $offlineObserved = $false
    $offlineDeadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 100
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 1 | Out-Null
        } catch {
            $offlineObserved = $true
        }
    } while (-not $offlineObserved -and (Get-Date) -lt $offlineDeadline)
    if (-not $offlineObserved) { throw "Packaged restore did not stop the API." }
    $onlineDeadline = (Get-Date).AddSeconds(30)
    $health = $null
    do {
        Start-Sleep -Milliseconds 250
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 1
            if ($health.status -eq "ok") { break }
        } catch {
            # The API is expected to be unavailable while the supervisor restores.
        }
    } while ((Get-Date) -lt $onlineDeadline)
    if (-not $health -or $health.status -ne "ok") { throw "Packaged restore did not restart the API." }
    $restoreStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/backups/status" -TimeoutSec 5
    if ($restoreStatus.restore_state -ne "succeeded") {
        throw "Packaged restore failed: $($restoreStatus.restore_message)"
    }

    $uninstaller = Join-Path $installRoot "Uninstall.exe"
    $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList "/S" -WindowStyle Hidden -Wait -PassThru
    if ($uninstallProcess.ExitCode -ne 0) { throw "Silent uninstall failed: $($uninstallProcess.ExitCode)" }
    $supervisor.WaitForExit(10 * 1000) | Out-Null
    Start-Sleep -Milliseconds 500
    if (Test-Path -LiteralPath $installRoot) { throw "Program directory remained after uninstall." }
    if (-not (Test-Path -LiteralPath $marker)) { throw "Business data was removed by uninstall." }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $marker).Hash -ne $markerHash) {
        throw "Business data changed during uninstall."
    }

    if ($otherSupervisor.HasExited -or (Invoke-RestMethod -Uri "http://127.0.0.1:$otherPort/api/v1/health" -TimeoutSec 2).status -ne "ok") {
        throw "Upgrade or uninstall stopped a separate installation."
    }
    Write-Output "Lifecycle smoke passed: install, start, upgrade, supervised backup restore, uninstall, data preserved, separate installation unaffected."
} finally {
    if ($supervisor -and -not $supervisor.HasExited) {
        $executable = Join-Path $installRoot "Zhiyi.exe"
        if (Test-Path -LiteralPath $executable) {
            & $executable stop --data-dir $dataRoot | Out-Null
        }
        $supervisor.WaitForExit(5 * 1000) | Out-Null
    }
    if ($otherSupervisor -and -not $otherSupervisor.HasExited) {
        & (Join-Path $distRoot "Zhiyi\ZhiyiCLI.exe") stop --data-dir $otherDataRoot | Out-Null
        $otherSupervisor.WaitForExit(10 * 1000) | Out-Null
    }
}
