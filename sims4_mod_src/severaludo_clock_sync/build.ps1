$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $sourceRoot)
$output = Join-Path $repoRoot "dist\SeveralUDOClockSync.ts4script"
$archive = Join-Path $repoRoot "dist\SeveralUDOClockSync.zip"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "severaludo_clock_sync_build"
$defaultCompiler = Join-Path $repoRoot "tools\python37\python.exe"
$compiler = if ($env:SIMS4_PYTHON37) { $env:SIMS4_PYTHON37 } else { $defaultCompiler }

if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "Python 3.7 is required. Set SIMS4_PYTHON37 to its python.exe path."
}

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $staging "severaludo_clock_sync") -Force | Out-Null
$source = Join-Path $sourceRoot "__init__.py"
$compiled = Join-Path $staging "severaludo_clock_sync\__init__.pyc"
& $compiler -c "import py_compile; py_compile.compile(r'$source', cfile=r'$compiled', doraise=True)"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $compiled)) {
    throw "Python 3.7 bytecode compilation failed."
}
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Force }
Compress-Archive -Path (Join-Path $staging "severaludo_clock_sync") -DestinationPath $archive
Move-Item -LiteralPath $archive -Destination $output
Copy-Item -LiteralPath (Join-Path $sourceRoot "SeveralUDOClockRelay.ps1") `
    -Destination (Join-Path $repoRoot "dist\SeveralUDOClockRelay.ps1") -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot "Start SeveralUDO Clock Relay.bat") `
    -Destination (Join-Path $repoRoot "dist\Start SeveralUDO Clock Relay.bat") -Force
Write-Output $output
