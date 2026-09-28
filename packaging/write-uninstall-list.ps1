param([Parameter(Mandatory)][string]$Payload, [Parameter(Mandatory)][string]$Output)
$ErrorActionPreference = 'Stop'
$payloadRoot = (Resolve-Path -LiteralPath $Payload).Path.TrimEnd('\')
$lines = [System.Collections.Generic.List[string]]::new()
$entries = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -Force)
foreach ($entry in $entries) {
    if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installer payload contains a reparse point.' }
}
foreach ($entry in $entries | Where-Object { -not $_.PSIsContainer }) {
    $relative = $entry.FullName.Substring($payloadRoot.Length + 1).Replace('$', '$$')
    $lines.Add('Delete /REBOOTOK "$INSTDIR\' + $relative + '"')
}
foreach ($entry in $entries | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length } -Descending) {
    $relative = $entry.FullName.Substring($payloadRoot.Length + 1).Replace('$', '$$')
    $lines.Add('RMDir /REBOOTOK "$INSTDIR\' + $relative + '"')
}
# RMDir deliberately has no /r: unknown files and user-created subfolders survive.
$lines.Add('Delete /REBOOTOK "$INSTDIR\Uninstall.exe"')
$lines.Add('RMDir /REBOOTOK "$INSTDIR"')
[IO.File]::WriteAllLines([IO.Path]::GetFullPath($Output), $lines, [Text.UTF8Encoding]::new($true))
