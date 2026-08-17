$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $sourceRoot)
$outputDir = Join-Path $repoRoot "dist\sims4_mods"
$stagingDir = Join-Path ([System.IO.Path]::GetTempPath()) "severaludo_hcr_historical_diseases"
$archivePath = Join-Path $outputDir "SeveralUDO_HCR_Historical_Diseases.zip"
$scriptPath = Join-Path $outputDir "SeveralUDO_HCR_Historical_Diseases.ts4script"

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
if (Test-Path -LiteralPath $stagingDir) {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $stagingDir "severaludo_hcr_historical_diseases") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot "__init__.py") -Destination (Join-Path $stagingDir "severaludo_hcr_historical_diseases\__init__.py")

if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
if (Test-Path -LiteralPath $scriptPath) { Remove-Item -LiteralPath $scriptPath -Force }
Compress-Archive -Path (Join-Path $stagingDir "severaludo_hcr_historical_diseases") -DestinationPath $archivePath
Move-Item -LiteralPath $archivePath -Destination $scriptPath
Copy-Item -LiteralPath (Join-Path $sourceRoot "README.md") -Destination (Join-Path $outputDir "SeveralUDO_HCR_Historical_Diseases_README.md") -Force

Write-Output $scriptPath
