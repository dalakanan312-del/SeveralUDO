$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root ".desktop_runtime"
$Python = Join-Path $Runtime "Scripts\python.exe"
Set-Location -LiteralPath $Root
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
  $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($null -eq $Launcher) { throw "Python 3 is required to build the desktop release." }
  & $Launcher.Source -3 -m venv $Runtime
  if ($LASTEXITCODE -ne 0) { throw "The desktop build environment could not be created." }
}
& $Python -m pip install --disable-pip-version-check -r "requirements-desktop.txt"
if ($LASTEXITCODE -ne 0) { throw "Desktop build dependencies could not be installed." }
# Keep the maintained spec: regenerating it drops its private-file exclusions.
& $Python -m PyInstaller --noconfirm --clean "Decades Tracker.spec"
if ($LASTEXITCODE -ne 0) { throw "Desktop build failed." }
foreach ($PrivateFile in @("config.json", "install_result.txt")) {
  $BundledPrivateFile = Join-Path $Root "dist\Decades Tracker\_internal\clock_bridge\$PrivateFile"
  if (Test-Path -LiteralPath $BundledPrivateFile) { throw "Private Clock Sync file was included in the desktop build: $PrivateFile" }
}
$AppPayload = Join-Path $Root "dist\Decades Tracker\_internal\app"
New-Item -ItemType Directory -Force -Path $AppPayload | Out-Null
# PyInstaller records these assets correctly in its analysis graph, but some
# Windows builds have produced an empty app payload directory during COLLECT.
# Copying the runtime templates and static assets explicitly makes the native
# bundle self-contained and prevents a blank local launch after installation.
foreach ($AssetFolder in @("templates", "static")) {
  $AssetDestination = Join-Path $AppPayload $AssetFolder
  New-Item -ItemType Directory -Force -Path $AssetDestination | Out-Null
  Get-ChildItem -LiteralPath (Join-Path "app" $AssetFolder) -Force |
    Copy-Item -Destination $AssetDestination -Recurse -Force
}
Copy-Item -LiteralPath "app\medieval_names.json" -Destination (Join-Path $AppPayload "medieval_names.json") -Force
Copy-Item -LiteralPath "app\game_localization_fallbacks.json" -Destination (Join-Path $AppPayload "game_localization_fallbacks.json") -Force
Copy-Item -LiteralPath "assets\README - Native Desktop.txt" -Destination "dist\Decades Tracker\START HERE - Decades Tracker.txt" -Force
Write-Output (Join-Path $Root "dist\Decades Tracker\Decades Tracker.exe")
