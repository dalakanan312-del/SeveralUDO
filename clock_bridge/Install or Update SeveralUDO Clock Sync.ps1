param(
    [switch]$KeepDuplicateCopies
)

$ErrorActionPreference = "Stop"
$source = [System.IO.Path]::GetFullPath($PSScriptRoot)
$documents = [Environment]::GetFolderPath("MyDocuments")
$mods = [System.IO.Path]::GetFullPath((Join-Path $documents "Electronic Arts\The Sims 4\Mods"))
$target = [System.IO.Path]::GetFullPath((Join-Path $mods "SeveralUDOClockSync"))
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = [System.IO.Path]::GetFullPath((Join-Path $mods "SeveralUDOClockSync Backups\$stamp"))
$backupParent = [System.IO.Path]::GetFullPath((Join-Path $mods "SeveralUDOClockSync Backups"))
$reportPath = Join-Path $source "install_result.txt"

if (Get-Process -Name "TS4_x64" -ErrorAction SilentlyContinue) {
    throw "Close The Sims 4 before replacing a Script Mod. The tracker may remain open."
}
if (-not $target.StartsWith($mods, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The calculated installation folder is outside The Sims 4 Mods folder."
}

$allowed = @(
    "SeveralUDOClockSync.ts4script",
    "SeveralUDOClockRelay.ps1",
    "Start SeveralUDO Clock Relay.bat",
    "Test SeveralUDO Clock Sync.bat",
    "Install or Update SeveralUDO Clock Sync.ps1",
    "Install or Update SeveralUDO Clock Sync.bat",
    "README - Install Clock Sync.txt",
    "TROUBLESHOOTING.txt"
)
foreach ($name in $allowed) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $name) -PathType Leaf)) {
        throw "The update kit is incomplete: $name is missing."
    }
}

$sourceIsTarget = $source.TrimEnd('\') -eq $target.TrimEnd('\')
if (-not $sourceIsTarget) {
    if (Test-Path -LiteralPath $target -PathType Container) {
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        Get-ChildItem -LiteralPath $target -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $backupRoot $_.Name) -Force
        }
    }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    foreach ($name in $allowed) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $target $name) -Force
    }
    $privateConfig = Join-Path $source "config.json"
    if (Test-Path -LiteralPath $privateConfig -PathType Leaf) {
        Copy-Item -LiteralPath $privateConfig -Destination (Join-Path $target "config.json") -Force
    }
}

$duplicateBackup = Join-Path $backupRoot "Duplicates"
$duplicates = @(
    Get-ChildItem -LiteralPath $mods -Filter "SeveralUDOClockSync.ts4script" -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object {
        $folder = [System.IO.Path]::GetFullPath($_.DirectoryName)
        $folder.TrimEnd('\') -ne $target.TrimEnd('\') -and
        -not $folder.StartsWith($backupParent, [System.StringComparison]::OrdinalIgnoreCase)
    }
)
$movedDuplicates = @()
if ($duplicates.Count -and -not $KeepDuplicateCopies) {
    New-Item -ItemType Directory -Path $duplicateBackup -Force | Out-Null
    foreach ($duplicate in $duplicates) {
        $safeName = ($duplicate.FullName.Substring($mods.Length).TrimStart('\') -replace '[\\/:*?"<>|]', '_')
        $destination = Join-Path $duplicateBackup $safeName
        Move-Item -LiteralPath $duplicate.FullName -Destination $destination -Force
        $movedDuplicates += $duplicate.FullName
    }
}

$lines = @(
    "SeveralUDO Clock Sync 2.2.6 installation completed.",
    "Installed folder: $target",
    "Existing installation backup: $(if (Test-Path -LiteralPath $backupRoot) { $backupRoot } else { 'Not needed' })",
    "Private config: $(if (Test-Path -LiteralPath (Join-Path $target 'config.json')) { 'Present and preserved' } else { 'Missing - download a private kit from the tracker' })",
    "Duplicate Script Mods found: $($duplicates.Count)",
    "Duplicate Script Mods moved to backup: $($movedDuplicates.Count)",
    "Completed: $([DateTimeOffset]::Now.ToString('o'))"
)
$lines | Set-Content -LiteralPath $reportPath -Encoding UTF8
$lines | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "Run 'Test SeveralUDO Clock Sync.bat' in the installed folder before starting the game."
