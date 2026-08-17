$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $sourceRoot)
$defaultCompiler = Join-Path $repoRoot "tools\python37\python.exe"
$compiler = if ($env:SIMS4_PYTHON37) { $env:SIMS4_PYTHON37 } else { $defaultCompiler }
$outputDir = Join-Path $repoRoot "dist\sims4_mods"
$stagingDir = Join-Path ([System.IO.Path]::GetTempPath()) "severaludo_hcr_historical_diseases"
$archivePath = Join-Path $outputDir "SeveralUDO_HCR_Historical_Diseases.zip"
$scriptPath = Join-Path $outputDir "SeveralUDO_HCR_Historical_Diseases.ts4script"

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
if (Test-Path -LiteralPath $stagingDir) {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $stagingDir "severaludo_hcr_historical_diseases") -Force | Out-Null
if (-not (Test-Path -LiteralPath $compiler)) {
    throw "The Sims 4 Python 3.7 compiler was not found at $compiler. Set SIMS4_PYTHON37 to a compatible python.exe."
}

$sourcePath = Join-Path $sourceRoot "__init__.py"
$compiledPath = Join-Path $stagingDir "severaludo_hcr_historical_diseases\__init__.pyc"
& $compiler -c "import py_compile; py_compile.compile(r'$sourcePath', cfile=r'$compiledPath', doraise=True)"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $compiledPath)) {
    throw "Failed to compile the add-on with The Sims 4's Python version."
}

if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
if (Test-Path -LiteralPath $scriptPath) { Remove-Item -LiteralPath $scriptPath -Force }
Compress-Archive -Path (Join-Path $stagingDir "severaludo_hcr_historical_diseases") -DestinationPath $archivePath
Move-Item -LiteralPath $archivePath -Destination $scriptPath
Copy-Item -LiteralPath (Join-Path $sourceRoot "README.md") -Destination (Join-Path $outputDir "SeveralUDO_HCR_Historical_Diseases_README.md") -Force

Write-Output $scriptPath
