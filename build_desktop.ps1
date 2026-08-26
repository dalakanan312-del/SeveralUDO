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
  --collect-all uvicorn --collect-all jinja2 --collect-all sqlalchemy `
  --collect-all webview --collect-all pythonnet --collect-all clr_loader `
  desktop_launcher.py
if ($LASTEXITCODE -ne 0) { throw "Desktop build failed." }
Copy-Item -LiteralPath "assets\README - Native Desktop.txt" -Destination "dist\Decades Tracker\START HERE - Decades Tracker.txt" -Force
Write-Output (Join-Path $Root "dist\Decades Tracker\Decades Tracker.exe")
