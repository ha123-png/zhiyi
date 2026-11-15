param(
    [Parameter(Mandatory)][string]$InstallDirectory,
    [Parameter(Mandatory)][string]$DataDirectory,
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Digest,
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][int]$DesktopPid
)

$ErrorActionPreference = 'Stop'
$cache = Split-Path -Parent $Installer
$log = Join-Path $cache 'last-install.log'
$failedMarker = Join-Path $cache 'install-failed'
$program = Join-Path $InstallDirectory 'Zhiyi.exe'
$cli = Join-Path $InstallDirectory 'ZhiyiCLI.exe'
$oldProgram = Join-Path (Split-Path -Parent $InstallDirectory) (".zhiyi-before-$Version")
$failedProgram = Join-Path (Split-Path -Parent $InstallDirectory) (".zhiyi-failed-$Version-" + [Guid]::NewGuid().ToString('N'))
$moved = $false
$allowLaunch = $true

function Check-RegularDirectory([string]$Path) {
    $entry = Get-Item -LiteralPath $Path
    if (!$entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $entry.FullName -ine [IO.Path]::GetFullPath($Path)) { throw '更新目录不符合要求。' }
}

try {
    $desktop = Get-Process -Id $DesktopPid -ErrorAction SilentlyContinue
    if ($desktop -and $desktop.Path -ieq $program -and !$desktop.WaitForExit(30000)) { throw '知意仍在运行。' }
    Check-RegularDirectory $cache
    Check-RegularDirectory $InstallDirectory
    if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -ine $Digest) { throw '安装包校验失败。' }
    $existing = Get-AuthenticodeSignature -LiteralPath $program
    if ($existing.Status -eq 'Valid') {
        $next = Get-AuthenticodeSignature -LiteralPath $Installer
        if ($next.Status -ne 'Valid' -or $next.SignerCertificate.Subject -ne $existing.SignerCertificate.Subject) { throw '安装包发布者校验失败。' }
    }
    $registered = Get-ItemPropertyValue -LiteralPath 'HKCU:\Software\Zhiyi' -Name InstallDir
    if ([IO.Path]::GetFullPath($registered) -ine [IO.Path]::GetFullPath($InstallDirectory)) { throw '安装位置与系统登记不一致。' }
    if (Test-Path -LiteralPath $oldProgram) { throw '已存在该版本的回退目录，请检查之前的更新。' }
    & $cli validate-installation --installation-dir $InstallDirectory --data-dir $DataDirectory
    if ($LASTEXITCODE) { throw '安装目录检查失败。' }
    $backup = Join-Path $DataDirectory ("backups\" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssffffffZ') + '.dpbak')
    & $cli backup --data-dir $DataDirectory --archive $backup
    if ($LASTEXITCODE -or !(Test-Path -LiteralPath $backup -PathType Leaf)) { throw '无法创建升级前备份。' }
    $registry = @('HKCU\Software\Zhiyi','HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Zhiyi')
    for ($index = 0; $index -lt $registry.Count; $index++) {
        & reg.exe export $registry[$index] (Join-Path $cache "before-$index.reg") /y | Out-Null
        if ($LASTEXITCODE) { throw '无法保存安装登记。' }
    }
    Move-Item -LiteralPath $InstallDirectory -Destination $oldProgram
    $moved = $true
    $arguments = '/S /D=' + $InstallDirectory
    $process = Start-Process -FilePath $Installer -ArgumentList $arguments -WorkingDirectory $cache -WindowStyle Hidden -PassThru
    if (!$process.WaitForExit(600000)) {
        $allowLaunch = $false
        throw '安装程序仍在运行。旧程序和备份已保留。'
    }
    if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $program -PathType Leaf) -or
        (Get-Item -LiteralPath $program).VersionInfo.ProductVersion -ne $Version) { throw '新版安装未完成。' }
    "已更新到 $Version。旧程序：$oldProgram。升级前备份：$backup" | Set-Content -LiteralPath $log -Encoding UTF8
    Remove-Item -LiteralPath $Installer,(Join-Path $cache 'download.json'),$failedMarker -ErrorAction SilentlyContinue
} catch {
    if ($moved -and $allowLaunch) {
        try {
            if (Test-Path -LiteralPath $InstallDirectory) {
                Check-RegularDirectory $InstallDirectory
                Move-Item -LiteralPath $InstallDirectory -Destination $failedProgram
            }
            Move-Item -LiteralPath $oldProgram -Destination $InstallDirectory
            for ($index = 0; $index -lt $registry.Count; $index++) {
                & reg.exe import (Join-Path $cache "before-$index.reg") | Out-Null
                if ($LASTEXITCODE) { throw '无法恢复安装登记。' }
            }
        } catch { $allowLaunch = $false }
    }
    "更新失败：$($_.Exception.Message)" | Set-Content -LiteralPath $log -Encoding UTF8
    New-Item -ItemType File -Path $failedMarker -Force | Out-Null
}

if ($allowLaunch -and (Test-Path -LiteralPath $program -PathType Leaf)) {
    Start-Process -FilePath $program -ArgumentList @('--data-dir', ('"' + $DataDirectory + '"'))
}
