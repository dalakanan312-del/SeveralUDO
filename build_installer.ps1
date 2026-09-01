$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CompilerCandidates = @(
  $env:INNO_SETUP_COMPILER,
  (Join-Path $Root "tools\InnoSetup\ISCC.exe"),
  (Join-Path $Root "tools\InnoSetup\tools\ISCC.exe"),
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$Compiler = $CompilerCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
$Script = Join-Path $Root "installer\DecadesTracker.iss"
if (-not $Compiler) { throw "Inno Setup 6 is required. Install it or set INNO_SETUP_COMPILER to ISCC.exe." }
if (-not (Test-Path -LiteralPath (Join-Path $Root "dist\Decades Tracker\Decades Tracker.exe") -PathType Leaf)) { throw "Build the desktop application first." }
& $Compiler $Script
if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed." }
$Installer = Join-Path $Root "release\Decades-Tracker-4.5.11-Setup.exe"
$Checksum = Get-FileHash -LiteralPath $Installer -Algorithm SHA256
$ChecksumFile = "$Installer.sha256"
Set-Content -LiteralPath $ChecksumFile -Encoding ASCII -Value ("{0} *{1}" -f $Checksum.Hash.ToLowerInvariant(), (Split-Path -Leaf $Installer))
Get-Item $Installer, $ChecksumFile
