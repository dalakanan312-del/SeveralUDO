$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $sourceRoot)
$output = Join-Path $repoRoot "dist\SeveralUDOClockSync.ts4script"
$archive = Join-Path $repoRoot "dist\SeveralUDOClockSync.zip"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "severaludo_clock_sync_build"

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $staging "severaludo_clock_sync") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot "__init__.py") -Destination (Join-Path $staging "severaludo_clock_sync\__init__.py")
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Force }
Compress-Archive -Path (Join-Path $staging "severaludo_clock_sync") -DestinationPath $archive
Move-Item -LiteralPath $archive -Destination $output
Write-Output $output
