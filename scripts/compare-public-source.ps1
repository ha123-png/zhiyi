param(
    [string]$PublicWorktree = "public-release/zhiyi"
)

$ErrorActionPreference = "Stop"

$privateRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$publicRoot = (Resolve-Path -LiteralPath (Join-Path $privateRoot $PublicWorktree)).Path
$expectedPublicRemote = "https://github.com/ha123-png/zhiyi.git"

$publicRemote = (git -C $publicRoot remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $publicRemote -ne $expectedPublicRemote) {
    throw "Public worktree origin is not $expectedPublicRemote"
}

if (git -C $publicRoot status --porcelain) {
    throw "Public worktree is dirty; refusing to compare an ambiguous release source."
}

$sourceRoots = @("apps", "packaging", "scripts")
$sourceFiles = @("VERSION", "package.json", "package-lock.json")
$differences = New-Object System.Collections.Generic.List[string]

function Get-ProductSourceFiles([string]$Root, [string]$SourceRoot, [bool]$Public) {
    # Match release_evidence.py's source discovery, including new non-ignored files.
    $raw = @(git -C $Root -c core.quotepath=false ls-files --cached --others --exclude-standard -z -- $SourceRoot)
    if ($LASTEXITCODE -ne 0) { throw "Cannot enumerate product source files." }
    $names = (($raw -join "`n") -split "`0") | Where-Object { $_ }
    foreach ($name in $names) {
        $generated = $name -match '(^|/)(\.git|\.local|\.venv|__pycache__|dist|node_modules|data|runtime|uploads|backups|private|keys)(/|$)' -or
            $name -match '\.(db(?:-(?:shm|wal))?|sqlite3?|dpbak|log|key|pem|p12|pfx)$' -or
            ($name -match '(^|/)\.env($|\.)' -and $name -notmatch '(^|/)\.env\.example$')
        if ($generated) {
            if ($Public) { throw "Public source contains an excluded runtime/private path: $name" }
            continue
        }
        $name
    }
}

function Assert-SafeSourcePath([string]$Root, [string]$RelativePath) {
    $boundary = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $fullPath = [IO.Path]::GetFullPath((Join-Path $boundary $RelativePath))
    if (-not $fullPath.StartsWith($boundary + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source path is outside its repository."
    }
    $cursor = $fullPath
    while ($cursor -and $cursor -ne $boundary) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Product source must not traverse links: $RelativePath"
        }
        $cursor = Split-Path -Parent $cursor
    }
}

function Compare-File([string]$relativePath) {
    Assert-SafeSourcePath $privateRoot $relativePath
    Assert-SafeSourcePath $publicRoot $relativePath
    $privatePath = Join-Path $privateRoot $relativePath
    $publicPath = Join-Path $publicRoot $relativePath

    if (-not (Test-Path -LiteralPath $privatePath)) {
        $differences.Add("missing-private: $relativePath")
        return
    }
    if (-not (Test-Path -LiteralPath $publicPath)) {
        $differences.Add("missing-public: $relativePath")
        return
    }

    $privateHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $privatePath).Hash
    $publicHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $publicPath).Hash
    if ($privateHash -ne $publicHash) {
        $differences.Add("content: $relativePath")
    }
}

foreach ($sourceRoot in $sourceRoots) {
    $privateFiles = @(Get-ProductSourceFiles $privateRoot $sourceRoot $false)
    $publicFiles = @(Get-ProductSourceFiles $publicRoot $sourceRoot $true)

    foreach ($relativePath in @($privateFiles + $publicFiles | Sort-Object -Unique)) {
        Compare-File $relativePath
    }
}

foreach ($relativePath in $sourceFiles) {
    Compare-File $relativePath
}

if ($differences.Count -gt 0) {
    Write-Host "Private/public product source differs:" -ForegroundColor Yellow
    $differences | Sort-Object -Unique | ForEach-Object { Write-Host "  $_" }
    exit 1
}

Write-Host "Private and public product source are byte-identical for the release allowlist."
