$ErrorActionPreference = "Stop"
$source = [System.IO.Path]::GetFullPath($PSScriptRoot)
$documents = [Environment]::GetFolderPath("MyDocuments")
$mods = [System.IO.Path]::GetFullPath((Join-Path $documents "Electronic Arts\The Sims 3\Mods"))
$target = [System.IO.Path]::GetFullPath((Join-Path $mods "SeveralUDOClockSync"))
$packageTarget = [System.IO.Path]::GetFullPath((Join-Path $mods "Packages\SeveralUDOSims3ClockSync.package"))
$files = @(
  "SeveralUDOClockRelay.ps1", "Start SeveralUDO Sims 3 Clock Relay.bat",
  "Report Sims 3 Clock Now.ps1", "Report Sims 3 Clock Now.bat",
  "Test SeveralUDO Sims 3 Clock Sync.bat",
  "Install or Update SeveralUDO Sims 3 Clock Sync.ps1",
  "Install or Update SeveralUDO Sims 3 Clock Sync.bat",
  "README - Install Sims 3 Clock Sync.txt", "TROUBLESHOOTING.txt",
  "SeveralUDOClockSync-Sims3-Source.cs", "SeveralUDOSims3ClockSync.package"
)
foreach ($name in $files) {
  if (-not (Test-Path -LiteralPath (Join-Path $source $name) -PathType Leaf)) { throw "The kit is incomplete: $name is missing." }
}
if (-not $target.StartsWith($mods, [System.StringComparison]::OrdinalIgnoreCase) -or -not $packageTarget.StartsWith($mods, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "The calculated installation folder is outside the Sims 3 Mods folder."
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $packageTarget) -Force | Out-Null
foreach ($name in $files) { Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $target $name) -Force }
Copy-Item -LiteralPath (Join-Path $source "SeveralUDOSims3ClockSync.package") -Destination $packageTarget -Force
$privateConfig = Join-Path $source "config.json"
if (Test-Path -LiteralPath $privateConfig -PathType Leaf) { Copy-Item -LiteralPath $privateConfig -Destination (Join-Path $target "config.json") -Force }
@(
  "SeveralUDO Sims 3 Clock Sync 1.0.0 installed.",
  "Relay folder: $target",
  "Automatic game package: $packageTarget",
  "The package reads only the active Sims 3 game clock; it does not modify a .sims3 save.",
  "Run Test SeveralUDO Sims 3 Clock Sync.bat, then Start SeveralUDO Sims 3 Clock Relay.bat.",
  "Open a save. The package writes its first clock snapshot shortly after loading, and the relay sends it automatically."
) | Set-Content -LiteralPath (Join-Path $source "install_result.txt") -Encoding UTF8
Get-Content -LiteralPath (Join-Path $source "install_result.txt")
