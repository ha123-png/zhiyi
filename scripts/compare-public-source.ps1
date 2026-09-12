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

function Compare-File([string]$relativePath) {
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
    $privateFiles = @(
        git -C $privateRoot ls-files -- $sourceRoot |
            Where-Object { $_ -notmatch "(^|/)(__pycache__|dist|node_modules)(/|$)" }
    )
    $publicFiles = @(
        git -C $publicRoot ls-files -- $sourceRoot |
            Where-Object { $_ -notmatch "(^|/)(__pycache__|dist|node_modules)(/|$)" }
    )

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
