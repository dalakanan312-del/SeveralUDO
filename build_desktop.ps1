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
& $Python -m PyInstaller --noconfirm --clean --windowed --name "Decades Tracker" `
  --noupx `
  --icon "assets\decades-app-icon.ico" `
  --version-file "assets\decades-version-info.txt" `
  --add-data "app\templates;app\templates" `
  --add-data "app\static;app\static" `
  --add-data "app\medieval_names.json;app" `
  --add-data "app\game_localization_fallbacks.json;app" `
  --add-data "assets\decades-app-icon.png;assets" `
  --add-data "assets\decades-app-icon.ico;assets" `
  --add-data "assets\loading.html;assets" `
  --add-data "clock_bridge;clock_bridge" `
  --add-data "clock_bridge_sims3;clock_bridge_sims3" `
  --collect-all uvicorn --collect-all jinja2 --collect-all sqlalchemy `
  --collect-all webview --collect-all pythonnet --collect-all clr_loader `
  desktop_launcher.py
if ($LASTEXITCODE -ne 0) { throw "Desktop build failed." }
$AppPayload = Join-Path $Root "dist\Decades Tracker\_internal\app"
New-Item -ItemType Directory -Force -Path $AppPayload | Out-Null
# PyInstaller records these assets correctly in its analysis graph, but some
# Windows builds have produced an empty app payload directory during COLLECT.
# Copying the runtime templates and static assets explicitly makes the native
# bundle self-contained and prevents a blank local launch after installation.
Copy-Item -LiteralPath "app\templates" -Destination (Join-Path $AppPayload "templates") -Recurse -Force
Copy-Item -LiteralPath "app\static" -Destination (Join-Path $AppPayload "static") -Recurse -Force
Copy-Item -LiteralPath "app\medieval_names.json" -Destination (Join-Path $AppPayload "medieval_names.json") -Force
Copy-Item -LiteralPath "app\game_localization_fallbacks.json" -Destination (Join-Path $AppPayload "game_localization_fallbacks.json") -Force
Copy-Item -LiteralPath "clock_bridge_sims3" -Destination (Join-Path $Root "dist\Decades Tracker\_internal\clock_bridge_sims3") -Recurse -Force
Copy-Item -LiteralPath "assets\README - Native Desktop.txt" -Destination "dist\Decades Tracker\START HERE - Decades Tracker.txt" -Force
Write-Output (Join-Path $Root "dist\Decades Tracker\Decades Tracker.exe")
