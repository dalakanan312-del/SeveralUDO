$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".test_runtime\python.exe"
Set-Location -LiteralPath $Root
& $Python -m PyInstaller --noconfirm --clean --windowed --name "Decades Tracker" `
  --icon "assets\decades-app-icon.ico" `
  --add-data "app\templates;app\templates" `
  --add-data "app\static;app\static" `
  --add-data "app\medieval_names.json;app" `
  --add-data "assets\decades-app-icon.png;assets" `
  --add-data "assets\loading.html;assets" `
  --add-data "clock_bridge\SeveralUDOClockSync.ts4script;clock_bridge" `
  --collect-all uvicorn --collect-all jinja2 --collect-all sqlalchemy `
  desktop_launcher.py
if ($LASTEXITCODE -ne 0) { throw "Desktop build failed." }
Write-Output (Join-Path $Root "dist\Decades Tracker\Decades Tracker.exe")
