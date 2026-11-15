#Requires -Version 7.0

param(
    [string]$Version = "",
    [string]$DistRoot = "",
    [string]$TestRoot = "",
    [string]$TemporaryParent = "",
    [string]$EvidenceParent = "",
    [int]$Port = 18767,
    [int]$ProcessTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectDirectory = Get-Item -LiteralPath $projectRoot -Force
$realProjectDirectory = $projectDirectory.ResolveLinkTarget($true)
if ($realProjectDirectory) { $projectRoot = [IO.Path]::GetFullPath($realProjectDirectory.FullName) }
$localRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot ".local"))
$evidenceBoundary = Join-Path $localRoot "evidence"
$temporaryBoundary = Join-Path $localRoot "tmp"
$evidenceParent = if ($EvidenceParent) { [IO.Path]::GetFullPath($EvidenceParent) } else { Join-Path $evidenceBoundary "v0.6.0\2026-09-30-production-lifecycle" }
$temporaryParent = if ($TemporaryParent) { [IO.Path]::GetFullPath($TemporaryParent) } else { Join-Path $temporaryBoundary "production-lifecycle" }
$python = Join-Path $projectRoot "apps\api\.venv\Scripts\python.exe"
$helper = Join-Path $PSScriptRoot "verify-packaged-business.py"
$runId = ""
$evidenceRoot = ""
$candidateReport = $null
$scriptFailure = $null
$supervisor = $null
$otherSupervisor = $null
$ownedInstances = [System.Collections.ArrayList]::new()
$processSequence = 0
$savedChildEnvironment = [ordered]@{}
$safeEnvironmentApplied = $false

function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-PathWithin([string]$Path, [string]$Root) {
    $fullPath = Get-FullPath $Path
    $fullRoot = Get-FullPath $Root
    return $fullPath.StartsWith($fullRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparseAncestors([string]$Path, [string]$Boundary) {
    $fullPath = Get-FullPath $Path
    $fullBoundary = Get-FullPath $Boundary
    if ($fullPath -ne $fullBoundary -and -not (Test-PathWithin $fullPath $fullBoundary)) {
        throw "Path is outside the allowed workspace root: $fullPath"
    }
    $relative = if ($fullPath -eq $fullBoundary) { "" } else { $fullPath.Substring($fullBoundary.Length + 1) }
    $cursor = $fullBoundary
    if (Test-Path -LiteralPath $cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Workspace boundary must not be a link: $cursor"
        }
    }
    foreach ($part in ($relative -split '[\\/]')) {
        if (-not $part) { continue }
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $entry = Get-Item -LiteralPath $cursor -Force
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Test paths must not traverse links: $cursor"
            }
        }
    }
}

function Save-Report {
    if (-not $script:Report -or -not $script:ReportPath) { return }
    $script:Report.updated_utc = (Get-Date).ToUniversalTime().ToString("o")
    $script:Report | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $script:ReportPath -Encoding utf8
}

function Invoke-Step([string]$Name, [scriptblock]$Action) {
    $step = [ordered]@{
        name = $Name
        status = "running"
        started_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
    $script:Report.steps = @($script:Report.steps) + @($step)
    Save-Report
    try {
        $value = & $Action
        $step.status = "passed"
        $step.finished_utc = (Get-Date).ToUniversalTime().ToString("o")
        if ($null -ne $value) { $step.result = $value }
        Save-Report
        return $value
    } catch {
        $step.status = "failed"
        $step.finished_utc = (Get-Date).ToUniversalTime().ToString("o")
        $step.error = $_.Exception.Message
        Save-Report
        throw
    }
}

function ConvertTo-ProcessArgument([object]$Value) {
    $text = [string]$Value
    if ($text -notmatch '[\s"]') { return $text }
    return '"' + $text.Replace('"', '\"') + '"'
}

function Invoke-BoundedProcess(
    [string]$Name,
    [string]$FilePath,
    [object[]]$ArgumentList,
    [int]$TimeoutSeconds,
    [string]$WorkingDirectory = $projectRoot
) {
    $script:processSequence++
    $safeName = $Name -replace '[^A-Za-z0-9._-]', '_'
    $stdout = Join-Path $script:evidenceRoot "$safeName-$script:processSequence.stdout.log"
    $stderr = Join-Path $script:evidenceRoot "$safeName-$script:processSequence.stderr.log"
    $arguments = (@($ArgumentList | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
    $process = Start-Process -FilePath $FilePath -ArgumentList $arguments -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        [void]$process.WaitForExit(5000)
        throw "$Name timed out after $TimeoutSeconds seconds; see $stdout and $stderr"
    }
    return [pscustomobject]@{
        process = $process
        exit_code = $process.ExitCode
        stdout = $stdout
        stderr = $stderr
    }
}

function Invoke-PythonHelper([string]$Name, [object[]]$Arguments, [string]$ReportFile) {
    $helperArguments = @($script:helper) + @($Arguments) + @("--report", $ReportFile)
    $result = Invoke-BoundedProcess -Name $Name -FilePath $script:python -ArgumentList $helperArguments `
        -TimeoutSeconds $script:ProcessTimeoutSeconds
    if (-not (Test-Path -LiteralPath $ReportFile)) {
        throw "$Name did not write its structured report; see $($result.stdout) and $($result.stderr)"
    }
    $payload = Get-Content -LiteralPath $ReportFile -Raw -Encoding utf8 | ConvertFrom-Json
    if ($result.exit_code -ne 0 -or $payload.status -ne "passed") {
        throw "$Name failed: $($payload.error); see $($result.stdout) and $($result.stderr)"
    }
    return $payload
}

function Get-ProcessImagePath([int]$Id) {
    $process = Get-Process -Id $Id -ErrorAction Stop
    $path = $process.Path
    if (-not $path) {
        try { $path = $process.MainModule.FileName } catch { }
    }
    if (-not $path) { throw "Cannot establish the executable path for PID $Id." }
    return Get-FullPath $path
}

function Assert-ProcessImage([System.Diagnostics.Process]$Process, [string]$ExpectedPath) {
    if ($Process.HasExited) { throw "Owned supervisor PID $($Process.Id) has exited." }
    $actualPath = Get-ProcessImagePath $Process.Id
    if (-not $actualPath.Equals((Get-FullPath $ExpectedPath), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Supervisor PID $($Process.Id) is running $actualPath instead of $ExpectedPath."
    }
}

function Get-WindowsProcessRecord([int]$Id) {
    $record = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $Id" -ErrorAction Stop
    if (-not $record -or -not $record.ExecutablePath) {
        throw "Cannot inspect executable and parent PID for process $Id."
    }
    return $record
}

function Assert-InstanceApiBinding($Instance, $State) {
    if ([int]$State.app_port -ne $Instance.port) {
        throw "Supervisor app_port '$($State.app_port)' does not match requested port $($Instance.port)."
    }
    $childPids = @($State.child_pids)
    if ($childPids.Count -lt 1 -or [int]$childPids[0] -le 4) {
        throw "Supervisor state does not identify a valid API child PID in child_pids[0]."
    }
    $apiPid = [int]$childPids[0]
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Instance.port -ErrorAction Stop |
        Where-Object { $_.LocalAddress -eq "127.0.0.1" })
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { [int]$_ })
    if ($owners.Count -ne 1 -or $owners[0] -ne $apiPid) {
        throw "Loopback port $($Instance.port) owner '$($owners -join ',')' does not match API PID $apiPid."
    }
    $apiProcess = Get-WindowsProcessRecord $apiPid
    if ([int]$apiProcess.ParentProcessId -ne $Instance.process.Id) {
        throw "API PID $apiPid parent PID $($apiProcess.ParentProcessId) does not match supervisor PID $($Instance.process.Id)."
    }
    $apiImage = Get-FullPath ([string]$apiProcess.ExecutablePath)
    if (-not $apiImage.Equals((Get-FullPath $Instance.executable), [StringComparison]::OrdinalIgnoreCase)) {
        throw "API PID $apiPid executable $apiImage does not match candidate executable $($Instance.executable)."
    }
    return [ordered]@{
        app_port = $Instance.port
        api_pid = $apiPid
        api_parent_pid = [int]$apiProcess.ParentProcessId
        api_executable = $apiImage
        listener_owner_pid = [int]$owners[0]
    }
}

function Assert-PortUnused([int]$TargetPort) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
        if ($connect.AsyncWaitHandle.WaitOne(350) -and $client.Connected) {
            throw "Loopback port $TargetPort is already in use; no existing process was changed."
        }
    } catch [System.Net.Sockets.SocketException] {
        # A refused loopback connection means no listener owns this port.
    } finally {
        $client.Dispose()
    }
}

function Start-OwnedSupervisor([string]$Name, [string]$Executable, [string]$DataRoot, [int]$TargetPort) {
    $script:processSequence++
    $safeName = $Name -replace '[^A-Za-z0-9._-]', '_'
    $stdout = Join-Path $script:evidenceRoot "$safeName-supervisor-$script:processSequence.stdout.log"
    $stderr = Join-Path $script:evidenceRoot "$safeName-supervisor-$script:processSequence.stderr.log"
    $args = @("--data-dir", $DataRoot, "--port", "$TargetPort", "--no-browser")
    $argumentLine = (@($args | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
    $process = Start-Process -FilePath $Executable -ArgumentList $argumentLine -WorkingDirectory (Split-Path -Parent $Executable) `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $instance = [pscustomobject]@{
        name = $Name
        process = $process
        executable = Get-FullPath $Executable
        cli = $null
        data_root = $DataRoot
        port = $TargetPort
        stdout = $stdout
        stderr = $stderr
    }
    [void]$script:ownedInstances.Add($instance)
    return $instance
}

function Wait-InstanceReady($Instance, [string]$ExpectedVersion, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastFailure = $null
    $statePath = Join-Path $Instance.data_root "runtime\supervisor.json"
    do {
        if ($Instance.process.HasExited) {
            throw "Supervisor PID $($Instance.process.Id) exited during startup; see $($Instance.stdout) and $($Instance.stderr)"
        }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($Instance.port)/api/v1/health" -TimeoutSec 1 -NoProxy
            if ($health.status -ne "ok" -or $health.version -ne $ExpectedVersion) {
                throw "Health returned version '$($health.version)' instead of '$ExpectedVersion'."
            }
            if (-not (Test-Path -LiteralPath $statePath)) {
                # Health can become ready just before the supervisor publishes its state.
                Start-Sleep -Milliseconds 100
                continue
            }
            $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
            if ([int]$state.pid -ne $Instance.process.Id) {
                Start-Sleep -Milliseconds 100
                continue
            }
            Assert-ProcessImage $Instance.process $Instance.executable
            $apiBinding = Assert-InstanceApiBinding $Instance $state
            return [pscustomobject]@{
                version = $health.version
                supervisor_pid = [int]$state.pid
                executable = $Instance.executable
                api = $apiBinding
            }
        } catch {
            $lastFailure = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $deadline)
    throw "Instance did not become candidate-ready within $TimeoutSeconds seconds. Last observation: $lastFailure"
}

function Stop-OwnedInstance($Instance) {
    $entry = [ordered]@{ name = $Instance.name; pid = $Instance.process.Id; status = "stopped" }
    if ($Instance.process.HasExited) {
        $entry.status = "already_exited"
        $entry.exit_code = $Instance.process.ExitCode
        return $entry
    }
    try {
        Assert-ProcessImage $Instance.process $Instance.executable
        $statePath = Join-Path $Instance.data_root "runtime\supervisor.json"
        $state = $null
        if (Test-Path -LiteralPath $statePath) {
            $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
        }
        if ($state -and [int]$state.pid -eq $Instance.process.Id) {
            $cli = $Instance.cli
            if (-not $cli -or -not (Test-Path -LiteralPath $cli)) {
                $cli = Join-Path $script:distRoot "Zhiyi\ZhiyiCLI.exe"
            }
            $stop = Invoke-BoundedProcess -Name "cleanup-$($Instance.name)" -FilePath $cli `
                -ArgumentList @("stop", "--data-dir", $Instance.data_root) -TimeoutSeconds 15
            if ($stop.exit_code -ne 0) { $entry.stop_cli_exit_code = $stop.exit_code }
        } else {
            $entry.stop_cli = "skipped; supervisor state no longer identifies this launched PID"
        }
        if (-not $Instance.process.WaitForExit(15000)) {
            $entry.forced_own_process_termination = $true
            $Instance.process.Kill()
            [void]$Instance.process.WaitForExit(5000)
        }
        if (-not $Instance.process.HasExited) { throw "Owned supervisor PID $($Instance.process.Id) did not exit after bounded cleanup." }
        $entry.exit_code = $Instance.process.ExitCode
        return $entry
    } catch {
        $entry.status = "failed"
        $entry.error = $_.Exception.Message
        return $entry
    }
}

function Save-SmallJson([string]$Path, [object]$Value) {
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Enter-SafeChildEnvironment([string]$IsolatedDataRoot) {
    $protectedNames = @(
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY"
    )
    foreach ($entry in Get-ChildItem Env:) {
        if ($entry.Name.StartsWith("DOCUMENT_PIPELINE_", [StringComparison]::OrdinalIgnoreCase) -or
            $protectedNames -contains $entry.Name.ToUpperInvariant()) {
            $script:savedChildEnvironment[$entry.Name] = $entry.Value
        }
    }
    $script:safeEnvironmentApplied = $true
    foreach ($name in @($script:savedChildEnvironment.Keys)) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    $env:DOCUMENT_PIPELINE_MODEL_PROVIDER = "openai_compatible"
    $env:DOCUMENT_PIPELINE_MODEL_NAME = "lifecycle-offline-sentinel"
    $env:DOCUMENT_PIPELINE_MODEL_BASE_URL = "http://127.0.0.1:1/v1"
    $env:DOCUMENT_PIPELINE_MODEL_API_KEY = ""
    $env:DOCUMENT_PIPELINE_DATA_DIR = $IsolatedDataRoot
    $env:DOCUMENT_PIPELINE_QUEUE_DB = Join-Path $IsolatedDataRoot "queue.db"
    $env:DOCUMENT_PIPELINE_INTEGRATION_READ_TOKEN = ""
    $env:DOCUMENT_PIPELINE_INTEGRATION_WRITE_TOKEN = ""
    $env:DOCUMENT_PIPELINE_DEVELOPMENT_ORIGINS = "0"
    foreach ($name in @(
        "DOCUMENT_PIPELINE_MCP_WRITE_ENABLED", "DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED",
        "DOCUMENT_PIPELINE_MCP_TASK_READ_ENABLED", "DOCUMENT_PIPELINE_MCP_TEMPLATE_READ_ENABLED",
        "DOCUMENT_PIPELINE_MCP_FILE_ACCESS_ENABLED", "DOCUMENT_PIPELINE_MCP_RESULT_READ_ENABLED",
        "DOCUMENT_PIPELINE_MCP_DATA_READ_ENABLED"
    )) { [Environment]::SetEnvironmentVariable($name, "0", "Process") }
    $env:NO_PROXY = "127.0.0.1,localhost,::1"
}

function Restore-ChildEnvironment {
    if (-not $script:safeEnvironmentApplied) { return }
    $protectedNames = @(
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY"
    )
    foreach ($entry in Get-ChildItem Env:) {
        if ($entry.Name.StartsWith("DOCUMENT_PIPELINE_", [StringComparison]::OrdinalIgnoreCase) -or
            $protectedNames -contains $entry.Name.ToUpperInvariant()) {
            Remove-Item -LiteralPath "Env:$($entry.Name)" -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in $script:savedChildEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $script:savedChildEnvironment[$name], "Process")
    }
    $script:safeEnvironmentApplied = $false
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "The workspace API virtualenv Python is missing: $python" }
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "The packaged-business verifier is missing: $helper" }
if ($Port -lt 1024 -or $Port -gt 65533) { throw "Port must be between 1024 and 65533 so its companion port is valid." }
if ($ProcessTimeoutSeconds -lt 15 -or $ProcessTimeoutSeconds -gt 600) { throw "ProcessTimeoutSeconds must be between 15 and 600." }

if (-not $DistRoot) { $DistRoot = Join-Path $localRoot "release\dist" }
if (-not (Test-Path -LiteralPath $DistRoot -PathType Container)) { throw "Candidate DistRoot is missing: $DistRoot" }
$script:distRoot = Get-FullPath (Resolve-Path -LiteralPath $DistRoot).Path
$distEntry = Get-Item -LiteralPath $script:distRoot -Force
if (($distEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Candidate DistRoot must not be a link." }

if (-not $TestRoot) {
    do {
        $runId = "$( (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') )-$([Guid]::NewGuid().ToString('N').Substring(0,8))"
        $testRootPath = Join-Path $temporaryParent $runId
        $evidencePath = Join-Path $evidenceParent $runId
    } while ((Test-Path -LiteralPath $testRootPath) -or (Test-Path -LiteralPath $evidencePath))
} else {
    $testRootPath = Get-FullPath $TestRoot
    $runId = Split-Path -Leaf $testRootPath
    $evidencePath = Join-Path $evidenceParent $runId
    if (Test-Path -LiteralPath $testRootPath) { throw "TestRoot already exists; preserve it and choose a new unique directory: $testRootPath" }
    if (Test-Path -LiteralPath $evidencePath) { throw "Evidence run already exists; choose a new TestRoot leaf: $evidencePath" }
}
$temporaryParent = Get-FullPath $temporaryParent
$evidenceParent = Get-FullPath $evidenceParent
if ($temporaryParent -ne (Get-FullPath $temporaryBoundary) -and -not (Test-PathWithin $temporaryParent $temporaryBoundary)) {
    throw "TemporaryParent must remain under this project's .local/tmp."
}
if ($evidenceParent -ne (Get-FullPath $evidenceBoundary) -and -not (Test-PathWithin $evidenceParent $evidenceBoundary)) {
    throw "EvidenceParent must remain under this project's .local/evidence."
}
if (-not (Test-PathWithin $testRootPath $temporaryParent)) { throw "TestRoot must be under the selected TemporaryParent." }
if (-not (Test-PathWithin $evidencePath $evidenceParent)) { throw "Evidence path must remain under this project's production-lifecycle evidence root." }
Assert-NoReparseAncestors $temporaryParent $projectRoot
Assert-NoReparseAncestors $evidenceParent $projectRoot
Assert-NoReparseAncestors $testRootPath $projectRoot
Assert-NoReparseAncestors $evidencePath $projectRoot
[void][IO.Directory]::CreateDirectory($temporaryParent)
[void][IO.Directory]::CreateDirectory($evidenceParent)
New-Item -ItemType Directory -Path $testRootPath -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Path $evidencePath -ErrorAction Stop | Out-Null
$script:evidenceRoot = $evidencePath
$script:ReportPath = Join-Path $script:evidenceRoot "lifecycle-report.json"
$script:helper = $helper
$script:python = $python
$script:ProcessTimeoutSeconds = $ProcessTimeoutSeconds
$script:Report = [ordered]@{
    schema_version = 1
    run_id = $runId
    status = "running"
    started_utc = (Get-Date).ToUniversalTime().ToString("o")
    project_root = $projectRoot
    dist_root = $script:distRoot
    test_root = $testRootPath
    evidence_root = $script:evidenceRoot
    requested_version = $Version
    port = $Port
    source = "candidate RELEASE_MANIFEST.json schema 2 + complete PAYLOAD_MANIFEST.json"
    harness = [ordered]@{
        powershell_script = $PSCommandPath
        powershell_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $PSCommandPath).Hash.ToLowerInvariant()
        python_helper = $helper
        python_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $helper).Hash.ToLowerInvariant()
    }
    steps = @()
    cleanup = @()
    boundaries = @(
        "NSIS SMOKE_MODE only; registry, Start Menu and desktop shortcuts are unverified",
        "SMOKE_MODE uninstall preserve-data path is exercised; /PURGE and interactive uninstall choices are unverified",
        "Native window, normal installed UI, old-version migration and public release/signature approval are unverified",
        "All business facts and originals are synthetic and isolated beneath this unique TestRoot"
    )
}
Save-Report

$installRoot = Join-Path $testRootPath "app"
$dataRoot = Join-Path $testRootPath "data"
$otherDataRoot = Join-Path $testRootPath "other-data"
$installerOutput = Join-Path $testRootPath "installer"
$checkpointPath = Join-Path $script:evidenceRoot "business-checkpoint.json"
$script:Version = $Version
$script:supervisor = $null
$script:otherSupervisor = $null

try {
    Enter-SafeChildEnvironment $dataRoot
    $candidateArgs = @("verify-candidate", "--dist-root", $script:distRoot)
    if ($Version) { $candidateArgs += @("--version", $Version) }
    $candidateReport = Join-Path $script:evidenceRoot "candidate-verification.json"
    $candidateResult = Invoke-Step "verify-candidate-manifests-and-complete-payload" {
        Invoke-PythonHelper "verify-candidate" $candidateArgs $candidateReport
    }
    $candidate = $candidateResult.candidate
    $script:Version = $candidate.version
    $script:Report.candidate = $candidate
    $script:Report.candidate_version = $candidate.version
    Save-Report

    $sourceBindingReport = Join-Path $script:evidenceRoot "smoke-installer-source-binding.json"
    $sourceBindingArgs = @(
        "verify-build-sources", "--dist-root", $script:distRoot,
        "--project-root", $projectRoot
    )
    foreach ($relativeSource in @(
        "packaging/installer.nsi", "packaging/purge-option.nsh", "packaging/app.ico", "packaging/uninstall.ico",
        "packaging/write-uninstall-list.ps1"
    )) { $sourceBindingArgs += @("--path", $relativeSource) }
    Invoke-Step "bind-smoke-installer-inputs-to-candidate-source" {
        Invoke-PythonHelper "verify-build-sources" $sourceBindingArgs $sourceBindingReport
    } | Out-Null

    if ($Version -and $Version -ne $candidate.version) { throw "Requested version does not match the bound candidate." }
    $releaseManifest = Get-Content -LiteralPath (Join-Path $script:distRoot "RELEASE_MANIFEST.json") -Raw -Encoding utf8 | ConvertFrom-Json
    $candidateSupervisor = Join-Path $script:distRoot "Zhiyi\Zhiyi.exe"
    $candidateCli = Join-Path $script:distRoot "Zhiyi\ZhiyiCLI.exe"
    if ((Get-Item -LiteralPath $candidateSupervisor).VersionInfo.ProductVersion -ne $candidate.version) {
        throw "Candidate Windows ProductVersion does not match the release manifest."
    }

    $cliVersionLog = Join-Path $script:evidenceRoot "candidate-cli-version.json"
    $cliResult = Invoke-Step "bind-candidate-cli-version" {
        $process = Invoke-BoundedProcess -Name "candidate-cli-version" -FilePath $candidateCli `
            -ArgumentList @("--version") -TimeoutSeconds 20
        if ($process.exit_code -ne 0) { throw "Candidate CLI --version exited $($process.exit_code)." }
        $output = Get-Content -LiteralPath $process.stdout -Raw -Encoding utf8
        if ($output -notmatch "(?m)(?:^|\s)$([regex]::Escape($candidate.version))\s*$") {
            throw "Candidate CLI did not report version $($candidate.version). Output: $output"
        }
        $record = [ordered]@{ version = $candidate.version; executable = $candidateCli; output = $output.Trim(); sha256 = (Get-FileHash -LiteralPath $candidateCli -Algorithm SHA256).Hash.ToLowerInvariant() }
        Save-SmallJson $cliVersionLog $record
        return $record
    }

    $compiler = $null
    $boundCompiler = $releaseManifest.toolchain.nsis_path
    if ($boundCompiler -and (Test-Path -LiteralPath $boundCompiler -PathType Leaf)) { $compiler = (Resolve-Path -LiteralPath $boundCompiler).Path }
    if (-not $compiler) {
        $knownCompiler = Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"
        if (Test-Path -LiteralPath $knownCompiler -PathType Leaf) { $compiler = (Resolve-Path -LiteralPath $knownCompiler).Path }
    }
    if (-not $compiler) {
        $command = Get-Command makensis.exe -ErrorAction SilentlyContinue
        if ($command) { $compiler = $command.Source }
    }
    if (-not $compiler) { throw "NSIS compiler is missing." }
    if ($boundCompiler -and -not (Get-FullPath $compiler).Equals((Get-FullPath $boundCompiler), [StringComparison]::OrdinalIgnoreCase)) {
        throw "NSIS compiler path differs from the candidate toolchain identity."
    }
    $licensePath = Join-Path (Split-Path -Parent $compiler) "COPYING"
    if (-not (Test-Path -LiteralPath $licensePath -PathType Leaf)) { throw "NSIS COPYING license is missing beside the selected compiler." }
    $compilerReportPath = Join-Path $script:evidenceRoot "nsis-compiler.json"
    $compilerRecord = Invoke-Step "verify-smoke-compiler-and-license-input" {
        $process = Invoke-BoundedProcess -Name "nsis-version" -FilePath $compiler `
            -ArgumentList @("/VERSION") -TimeoutSeconds 15
        if ($process.exit_code -ne 0) { throw "makensis /VERSION exited $($process.exit_code)." }
        $output = Get-Content -LiteralPath $process.stdout -Raw -Encoding utf8
        $match = [regex]::Match($output, "(?i)(?:v)?(\d+\.\d+)")
        if (-not $match.Success) { throw "Could not parse NSIS version output: $output" }
        $actualVersion = $match.Groups[1].Value
        $expectedVersion = [string]$releaseManifest.toolchain.nsis_version
        if ($expectedVersion -and $expectedVersion -notmatch [regex]::Escape($actualVersion)) {
            throw "NSIS version $actualVersion differs from candidate toolchain '$expectedVersion'."
        }
        if (-not $expectedVersion) { throw "Candidate manifest does not bind the NSIS version used to build it." }
        $record = [ordered]@{
            path = Get-FullPath $compiler
            version = $actualVersion
            build_manifest_version = $expectedVersion
            sha256 = (Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash.ToLowerInvariant()
            copying_path = $licensePath
            copying_sha256 = (Get-FileHash -LiteralPath $licensePath -Algorithm SHA256).Hash.ToLowerInvariant()
            output = $output.Trim()
        }
        Save-SmallJson $compilerReportPath $record
        return $record
    }

    $otherPort = $Port + 1
    Invoke-Step "ensure-loopback-ports-unused" {
        Assert-PortUnused $Port
        Assert-PortUnused $otherPort
        return [ordered]@{ installed_port = $Port; separate_instance_port = $otherPort; existing_processes_changed = $false }
    } | Out-Null

    New-Item -ItemType Directory -Path $installerOutput -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path $dataRoot -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path $otherDataRoot -ErrorAction Stop | Out-Null
    $installer = Join-Path $installerOutput "Zhiyi-$($candidate.version)-win-x64-smoke-setup.exe"
    $script:installer = $installer
    $script:installRoot = $installRoot
    Invoke-Step "compile-isolated-smoke-mode-installer" {
        $process = Invoke-BoundedProcess -Name "smoke-installer-build" -FilePath $compiler -ArgumentList @(
            "/INPUTCHARSET", "UTF8", "/DAPP_VERSION=$($candidate.version)", "/DBUILD_ROOT=$($script:distRoot)",
            "/DOUTPUT_DIR=$installerOutput", "/DSMOKE_MODE", "/DSMOKE_DATA_DIR=$dataRoot",
            (Join-Path $projectRoot "packaging\installer.nsi")
        ) -TimeoutSeconds 180
        if ($process.exit_code -ne 0 -or -not (Test-Path -LiteralPath $installer -PathType Leaf)) {
            throw "SMOKE_MODE installer compilation failed with exit $($process.exit_code); see $($process.stdout) and $($process.stderr)"
        }
        return [ordered]@{ path = $installer; size_bytes = (Get-Item -LiteralPath $installer).Length; sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant(); stdout = $process.stdout; stderr = $process.stderr }
    } | Out-Null

    $otherSupervisor = Start-OwnedSupervisor "separate-candidate-instance" $candidateSupervisor $otherDataRoot $otherPort
    $otherSupervisor.cli = $candidateCli
    $otherReady = Invoke-Step "start-and-bind-separate-candidate-instance" {
        Wait-InstanceReady $otherSupervisor $candidate.version 30
    }

    function Install-SmokePackage {
        $process = Invoke-BoundedProcess -Name "smoke-install" -FilePath $script:installer `
            -ArgumentList @("/S", "/D=$script:installRoot") -TimeoutSeconds $script:ProcessTimeoutSeconds
        if ($process.exit_code -ne 0) { throw "Silent install/overwrite exited $($process.exit_code); see $($process.stdout) and $($process.stderr)" }
        foreach ($name in @("Zhiyi.exe", "ZhiyiCLI.exe", "Uninstall.exe")) {
            if (-not (Test-Path -LiteralPath (Join-Path $script:installRoot $name) -PathType Leaf)) { throw "Installed package file is missing: $name" }
        }
        $payloadReport = Join-Path $script:evidenceRoot "installed-payload-$script:processSequence.json"
        $payload = Invoke-PythonHelper "verify-installed-payload" @(
            "verify-installed", "--install-root", $script:installRoot,
            "--payload-manifest", (Join-Path $script:distRoot "PAYLOAD_MANIFEST.json")
        ) $payloadReport
        return [pscustomobject]@{ exit_code = $process.exit_code; payload = $payload.installed_payload; stdout = $process.stdout; stderr = $process.stderr }
    }

    Invoke-Step "first-candidate-install-and-complete-payload-check" { Install-SmokePackage } | Out-Null
    $supervisor = Start-OwnedSupervisor "installed-candidate-instance" (Join-Path $installRoot "Zhiyi.exe") $dataRoot $Port
    $supervisor.cli = Join-Path $installRoot "ZhiyiCLI.exe"
    $initialReady = Invoke-Step "start-and-bind-installed-candidate" { Wait-InstanceReady $supervisor $candidate.version 30 }

    $prepareReport = Join-Path $script:evidenceRoot "business-prepare.json"
    $prepareArguments = @(
        "prepare-business", "--base-url", "http://127.0.0.1:$Port", "--version", $candidate.version,
        "--supervisor-state", (Join-Path $dataRoot "runtime\supervisor.json"),
        "--supervisor-pid", "$($supervisor.process.Id)", "--candidate-executable", $supervisor.executable,
        "--checkpoint", $checkpointPath
    )
    $businessPrepared = Invoke-Step "api-import-original-current-revision-and-create-backup" {
        Invoke-PythonHelper "prepare-business" $prepareArguments $prepareReport
    }

    Invoke-Step "same-version-overwrite-and-complete-payload-check" {
        $oldProcess = $supervisor.process
        $result = Install-SmokePackage
        if (-not $oldProcess.WaitForExit(15000)) { throw "Same-version overwrite did not stop its previous supervisor PID $($oldProcess.Id)." }
        if (-not $otherSupervisor -or $otherSupervisor.process.HasExited) { throw "Same-version overwrite stopped the independent candidate instance." }
        $result | Add-Member -NotePropertyName stopped_supervisor_pid -NotePropertyValue $oldProcess.Id
        return $result
    } | Out-Null

    $supervisor = Start-OwnedSupervisor "installed-candidate-after-overwrite" (Join-Path $installRoot "Zhiyi.exe") $dataRoot $Port
    $supervisor.cli = Join-Path $installRoot "ZhiyiCLI.exe"
    Invoke-Step "verify-business-after-same-version-overwrite" {
        $ready = Wait-InstanceReady $supervisor $candidate.version 30
        $report = Join-Path $script:evidenceRoot "business-after-overwrite.json"
        $result = Invoke-PythonHelper "verify-business" @(
            "verify-business", "--base-url", "http://127.0.0.1:$Port", "--version", $candidate.version,
            "--supervisor-state", (Join-Path $dataRoot "runtime\supervisor.json"),
            "--supervisor-pid", "$($supervisor.process.Id)", "--candidate-executable", $supervisor.executable,
            "--checkpoint", $checkpointPath
        ) $report
        return [ordered]@{ identity = $ready; business = $result }
    } | Out-Null

    Invoke-Step "mutate-after-backup-and-restore-through-packaged-supervisor" {
        $report = Join-Path $script:evidenceRoot "business-after-restore.json"
        $result = Invoke-PythonHelper "restore-business" @(
            "restore-business", "--base-url", "http://127.0.0.1:$Port", "--version", $candidate.version,
            "--supervisor-state", (Join-Path $dataRoot "runtime\supervisor.json"),
            "--supervisor-pid", "$($supervisor.process.Id)", "--candidate-executable", $supervisor.executable,
            "--checkpoint", $checkpointPath,
            "--timeout-seconds", "45"
        ) $report
        return $result
    } | Out-Null

    Invoke-Step "verify-separate-instance-before-uninstall" {
        if ($otherSupervisor.process.HasExited) { throw "Independent candidate instance exited before uninstall." }
        Assert-ProcessImage $otherSupervisor.process $candidateSupervisor
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$otherPort/api/v1/health" -TimeoutSec 3 -NoProxy
        $state = Get-Content -LiteralPath (Join-Path $otherDataRoot "runtime\supervisor.json") -Raw -Encoding utf8 | ConvertFrom-Json
        if ($health.status -ne "ok" -or $health.version -ne $candidate.version -or [int]$state.pid -ne $otherSupervisor.process.Id) {
            throw "Independent candidate process identity or health changed before uninstall."
        }
        Assert-ProcessImage $otherSupervisor.process $otherSupervisor.executable
        $apiBinding = Assert-InstanceApiBinding $otherSupervisor $state
        return [ordered]@{ version = $health.version; supervisor_pid = [int]$state.pid; api = $apiBinding }
    } | Out-Null

    $uninstaller = Join-Path $installRoot "Uninstall.exe"
    Invoke-Step "smoke-mode-uninstall-retains-user-data" {
        $oldProcess = $supervisor.process
        $uninstall = Invoke-BoundedProcess -Name "smoke-uninstall" -FilePath $uninstaller `
            -ArgumentList @("/S") -TimeoutSeconds $script:ProcessTimeoutSeconds
        if ($uninstall.exit_code -ne 0) { throw "Silent SMOKE_MODE uninstall exited $($uninstall.exit_code)." }
        if (-not $oldProcess.WaitForExit(15000)) { throw "SMOKE_MODE uninstall did not stop its installed supervisor." }
        # The uninstaller launcher can exit before its temporary child finishes
        # deleting the program tree. Require the final state within a bounded
        # deadline instead of treating the launcher's exit as teardown completion.
        $uninstallWait = [Diagnostics.Stopwatch]::StartNew()
        while ((Test-Path -LiteralPath $installRoot) -and $uninstallWait.Elapsed.TotalSeconds -lt $script:ProcessTimeoutSeconds) {
            Start-Sleep -Milliseconds 200
        }
        $uninstallWait.Stop()
        if (Test-Path -LiteralPath $installRoot) { throw "Program directory remained after the bounded SMOKE_MODE uninstall wait." }
        if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) { throw "SMOKE_MODE uninstall removed the isolated data directory." }
        if ($otherSupervisor.process.HasExited) { throw "Uninstall stopped the independent candidate instance." }
        $offlineReport = Join-Path $script:evidenceRoot "business-after-uninstall.json"
        $offline = Invoke-PythonHelper "verify-offline-business" @(
            "verify-offline-business", "--data-root", $dataRoot, "--checkpoint", $checkpointPath
        ) $offlineReport
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$otherPort/api/v1/health" -TimeoutSec 3 -NoProxy
        if ($health.status -ne "ok" -or $health.version -ne $candidate.version) { throw "Uninstall altered the independent instance health." }
        return [ordered]@{
            exit_code = $uninstall.exit_code
            stopped_supervisor_pid = $oldProcess.Id
            program_directory_removed = $true
            program_removal_wait_ms = $uninstallWait.ElapsedMilliseconds
            data_directory_retained = $true
            offline_business = $offline
            independent_instance_version = $health.version
            independent_instance_pid = $otherSupervisor.process.Id
        }
    } | Out-Null

    $script:Report.status = "cleaning_up"
    Save-Report
} catch {
    $scriptFailure = $_
    $script:Report.status = "failed"
    $script:Report.error = $_.Exception.Message
    $script:Report.failed_utc = (Get-Date).ToUniversalTime().ToString("o")
    Save-Report
} finally {
    for ($index = $ownedInstances.Count - 1; $index -ge 0; $index--) {
        $instance = $ownedInstances[$index]
        $cleanup = Stop-OwnedInstance $instance
        $script:Report.cleanup = @($script:Report.cleanup) + @($cleanup)
    }
    $cleanupFailures = @($script:Report.cleanup | Where-Object { $_.status -eq "failed" })
    if ($cleanupFailures.Count -gt 0) {
        $cleanupMessage = "One or more owned supervisor processes could not be cleaned up: " +
            (($cleanupFailures | ForEach-Object { "$($_.name): $($_.error)" }) -join "; ")
        if (-not $scriptFailure) { $scriptFailure = [Exception]::new($cleanupMessage) }
        $script:Report.status = "failed"
        $script:Report.cleanup_error = $cleanupMessage
        if (-not $script:Report.error) { $script:Report.error = $cleanupMessage }
    }
    Restore-ChildEnvironment
    if ($cleanupFailures.Count -eq 0 -and -not $scriptFailure) {
        $script:Report.status = "passed"
        $script:Report.completed_utc = (Get-Date).ToUniversalTime().ToString("o")
        $script:Report.cleanup_completed_utc = $script:Report.completed_utc
    } elseif (-not $script:Report.failed_utc) {
        $script:Report.failed_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
    Save-Report
}

if ($scriptFailure) { throw $scriptFailure }
Write-Output "Lifecycle smoke passed for candidate $($script:Version). Evidence: $($script:ReportPath)"
