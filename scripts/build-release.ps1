param(
    [string]$Version = "",
    [switch]$BuildInstaller,
    [switch]$AllowDirty,
    [switch]$RequireSignature,
    [string]$CertificateThumbprint = "",
    [string]$TimestampServer = "http://timestamp.acs.microsoft.com"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$canonicalVersion = (Get-Content -Raw -Encoding utf8 (Join-Path $projectRoot "VERSION")).Trim()
if (-not $Version) { $Version = $canonicalVersion }
if ($Version -ne $canonicalVersion) {
    throw "Requested version $Version does not match canonical VERSION $canonicalVersion."
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') {
    throw "VERSION must contain three or four numeric parts."
}

$versionSources = @(
    @{ Path = "apps\api\pyproject.toml"; Pattern = "version = `"$([regex]::Escape($Version))`"" },
    @{ Path = "apps\api\src\document_pipeline_api\version.py"; Pattern = "__version__ = `"$([regex]::Escape($Version))`"" },
    @{ Path = "apps\web\package.json"; Pattern = "`"version`": `"$([regex]::Escape($Version))`"" }
)
foreach ($source in $versionSources) {
    $content = Get-Content -Raw -Encoding utf8 (Join-Path $projectRoot $source.Path)
    if ($content -notmatch $source.Pattern) {
        throw "Version source $($source.Path) does not match canonical VERSION $Version."
    }
}

$revision = (git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') {
    throw "Cannot resolve the source Git revision."
}
$dirtyLines = @(git -C $projectRoot status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "Cannot inspect the source worktree." }
$isDirty = $dirtyLines.Count -gt 0
if ($isDirty -and -not $AllowDirty) {
    throw "Release builds require a clean Git worktree. Commit or stash changes, or use -AllowDirty for a non-releasable engineering build."
}

# Resolve the alembic migration head at build time instead of hardcoding it,
# so new migrations never need a manual sync. Multiple heads make
# get_current_head() fail, which prevents recording an ambiguous head.
# All paths are built from $projectRoot: this script may run via
# powershell -File from another working directory, and relative paths make
# uv exit silently with no output, which was misread as an empty head.
# NOTE: keep comments ASCII-only; PowerShell 5.1 reads .ps1 as ANSI unless a
# BOM is present, and UTF-8 Chinese comments corrupt parsing of the next line.
$alembicIni = (Join-Path $projectRoot "apps\api\alembic.ini").Replace('\', '/')
$savedErrorActionPreference = $ErrorActionPreference
try {
    # PowerShell 5.1 wraps any native stderr output in NativeCommandError when
    # ErrorActionPreference is Stop. uv writes harmless build progress there.
    $ErrorActionPreference = "Continue"
    $migrationHead = (& uv --cache-dir (Join-Path $projectRoot ".uv-cache") run --project (Join-Path $projectRoot "apps/api") python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(Config('$alembicIni')).get_current_head())" 2>$null)
} finally {
    $ErrorActionPreference = $savedErrorActionPreference
}
if ($LASTEXITCODE -ne 0 -or -not $migrationHead) {
    throw "Cannot resolve the current alembic migration head."
}

$signingCertificate = $null
if ($CertificateThumbprint) {
    $normalizedThumbprint = $CertificateThumbprint.Replace(" ", "").ToUpperInvariant()
    $signingCertificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -CodeSigningCert | Where-Object {
        $_.Thumbprint -eq $normalizedThumbprint -and $_.NotBefore -le (Get-Date) -and $_.NotAfter -gt (Get-Date)
    } | Select-Object -First 1
    if (-not $signingCertificate) {
        throw "A valid code-signing certificate with thumbprint $normalizedThumbprint was not found."
    }
}

function Sign-ReleaseArtifact([string]$Path) {
    if (-not $signingCertificate) { return }
    $result = Set-AuthenticodeSignature -LiteralPath $Path -Certificate $signingCertificate -HashAlgorithm SHA256 -TimestampServer $TimestampServer
    if ($result.Status -ne "Valid") {
        throw "Authenticode signing failed for ${Path}: $($result.StatusMessage)"
    }
}

$releaseRoot = Join-Path $projectRoot ".local\release"
$distRoot = Join-Path $releaseRoot "dist"
$workRoot = Join-Path $releaseRoot "build"
$resourceRoot = Join-Path $releaseRoot "resources"
$auditRoot = Join-Path $releaseRoot "audit"
$specPath = Join-Path $projectRoot "packaging\DocumentPipeline.spec"

if (-not $releaseRoot.StartsWith((Join-Path $projectRoot ".local\release"))) {
    throw "Refusing to clean a release directory outside the workspace."
}
if (Test-Path -LiteralPath $releaseRoot) {
    Remove-Item -LiteralPath $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $resourceRoot, $auditRoot -Force | Out-Null

Push-Location $projectRoot
try {
    $npmCache = Join-Path $projectRoot ".npm-cache"
    New-Item -ItemType Directory -Path $npmCache -Force | Out-Null
    # Reproducible build: npm ci installs exactly from the lockfile (it fails
    # when package.json and the lockfile disagree, which also verifies the web
    # version recorded in the lockfile), then the frontend bundle is built.
    npm.cmd ci --cache $npmCache
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency install (npm ci) failed." }
    npm.cmd --workspace apps/web run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }

    $npmAudit = Join-Path $auditRoot "npm-audit.json"
    npm.cmd audit --cache $npmCache --omit=dev --json | Set-Content -Encoding utf8 -LiteralPath $npmAudit
    if ($LASTEXITCODE -ne 0) { throw "npm production dependency audit failed." }

    $requirements = Join-Path $auditRoot "python-requirements.txt"
    uv export --cache-dir .uv-cache --project apps/api --frozen --no-dev --no-emit-project --format requirements-txt --output-file $requirements | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Python release dependency export failed." }
    $pythonAudit = Join-Path $auditRoot "pip-audit.json"
    # --disable-pip: the input is already a uv --frozen locked list, so there is
    # no need for pip-audit to re-run the pip resolver over it. The default
    # resolver is pathologically slow (10+ min, 100% CPU) on hash-locked files
    # and stalls the build; auditing the locked list directly finishes in seconds
    # while --require-hashes still verifies every declared hash.
    # Use pip-audit's default cache (no --cache-dir): pointing it at an empty
    # local dir forces a full OSV vulnerability-database download on every build,
    # which stalls behind slow CDN connectivity; the default cache is reused.
    uv --cache-dir .uv-cache run --project apps/api pip-audit --disable-pip --require-hashes -r $requirements --format json --output $pythonAudit
    if ($LASTEXITCODE -ne 0) { throw "Python production dependency audit failed." }

    uv --cache-dir .uv-cache run --project apps/api python scripts/collect-third-party-notices.py --root $projectRoot --output $resourceRoot --version $Version --revision $revision --fail-on-policy
    if ($LASTEXITCODE -ne 0) { throw "Third-party license/SBOM policy failed." }
    Copy-Item -LiteralPath $npmAudit, $pythonAudit -Destination $resourceRoot

    $buildMetadata = [ordered]@{
        product = "Document Pipeline"
        version = $Version
        git_revision = $revision
        source_dirty = $isDirty
        migration_head = $migrationHead
        builder_os = [System.Environment]::OSVersion.VersionString
        architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    }
    $buildMetadata | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $resourceRoot "BUILD_METADATA.json")

    $env:DOCUMENT_PIPELINE_RELEASE_RESOURCES = $resourceRoot
    $env:DOCUMENT_PIPELINE_BUILD_VERSION = $Version
    uv --cache-dir .uv-cache run --project apps/api pyinstaller --noconfirm --clean --distpath $distRoot --workpath $workRoot $specPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $executable = Join-Path $distRoot "Zhiyi\ZhiyiCLI.exe"
    if (-not (Test-Path -LiteralPath $executable)) { throw "Release executable is missing." }
    $reportedVersion = (& $executable --version | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or $reportedVersion -notmatch "\s$([regex]::Escape($Version))$") {
        throw "Release version mismatch: requested $Version, executable reported '$reportedVersion'."
    }
    $windowsVersion = (Get-Item -LiteralPath $executable).VersionInfo.ProductVersion
    if ($windowsVersion -ne $Version) {
        throw "Windows ProductVersion mismatch: expected $Version, got '$windowsVersion'."
    }
    Sign-ReleaseArtifact (Join-Path $distRoot "Zhiyi\Zhiyi.exe")
    Sign-ReleaseArtifact (Join-Path $distRoot "Zhiyi\ZhiyiCLI.exe")

    if ($BuildInstaller) {
        $makensisCommand = Get-Command makensis.exe -CommandType Application -ErrorAction SilentlyContinue
        $makensis = if ($makensisCommand) { $makensisCommand.Source } else { $null }
        if (-not $makensis) {
            $standardMakensis = Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"
            if (Test-Path -LiteralPath $standardMakensis) { $makensis = $standardMakensis }
        }
        if (-not $makensis) {
            $workspaceMakensis = Join-Path $projectRoot ".local\tools\nsis\makensis.exe"
            if (Test-Path -LiteralPath $workspaceMakensis) { $makensis = $workspaceMakensis }
        }
        if (-not $makensis) { throw "NSIS compiler makensis.exe was not found." }
        $installerOutput = Join-Path $distRoot "installer"
        New-Item -ItemType Directory -Path $installerOutput -Force | Out-Null
        & $makensis "/INPUTCHARSET" "UTF8" "/DAPP_VERSION=$Version" "/DBUILD_ROOT=$distRoot" "/DOUTPUT_DIR=$installerOutput" (Join-Path $projectRoot "packaging\installer.nsi")
        if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
        Sign-ReleaseArtifact (Join-Path $installerOutput "Zhiyi-$Version-win-x64-setup.exe")
    }

    $hashTargets = @("Zhiyi\Zhiyi.exe", "Zhiyi\ZhiyiCLI.exe")
    if ($BuildInstaller) { $hashTargets += "installer\Zhiyi-$Version-win-x64-setup.exe" }
    $artifacts = foreach ($relativePath in $hashTargets) {
        $target = Join-Path $distRoot $relativePath
        $signature = Get-AuthenticodeSignature -LiteralPath $target
        [ordered]@{
            path = $relativePath.Replace('\', '/')
            size_bytes = (Get-Item -LiteralPath $target).Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
            signature_status = $signature.Status.ToString()
            signer_subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
        }
    }
    if ($RequireSignature -and @($artifacts | Where-Object signature_status -ne "Valid").Count -gt 0) {
        throw "Release signature gate failed. Every EXE and installer must have a valid Authenticode signature."
    }
    $manifest = [ordered]@{
        schema_version = 1
        product = "Document Pipeline"
        version = $Version
        git_revision = $revision
        source_dirty = $isDirty
        releasable = (-not $isDirty) -and (@($artifacts | Where-Object signature_status -ne "Valid").Count -eq 0)
        migration_head = $migrationHead
        artifacts = @($artifacts)
        evidence = @("resources/SBOM.cdx.json", "resources/LICENSE_POLICY.json", "resources/npm-audit.json", "resources/pip-audit.json")
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $distRoot "RELEASE_MANIFEST.json")
    Copy-Item -LiteralPath $resourceRoot -Destination (Join-Path $distRoot "resources") -Recurse
    $artifacts | ForEach-Object { "$($_.sha256)  $($_.path)" } | Set-Content -Encoding ascii -LiteralPath (Join-Path $distRoot "SHA256SUMS.txt")
} finally {
    Remove-Item Env:DOCUMENT_PIPELINE_RELEASE_RESOURCES -ErrorAction SilentlyContinue
    Remove-Item Env:DOCUMENT_PIPELINE_BUILD_VERSION -ErrorAction SilentlyContinue
    Pop-Location
}

Write-Output "Release directory: $distRoot"
