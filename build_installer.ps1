$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Compiler = Join-Path $Root "tools\InnoSetup\ISCC.exe"
$Script = Join-Path $Root "installer\DecadesTracker.iss"
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) { throw "Inno Setup compiler is not installed in tools\InnoSetup." }
if (-not (Test-Path -LiteralPath (Join-Path $Root "dist\Decades Tracker\Decades Tracker.exe") -PathType Leaf)) { throw "Build the desktop application first." }
& $Compiler $Script
if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed." }
Get-Item (Join-Path $Root "release\Decades-Tracker-4.2.3-Setup.exe")
