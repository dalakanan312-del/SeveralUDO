$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $AppDir
$Runtime = Join-Path $AppDir ".runtime"
$Python = Join-Path $Runtime "python.exe"
$Ready = Join-Path $Runtime ".v4-ready"
if (-not (Test-Path -LiteralPath $Ready)) {
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    $Version = "3.12.10"
    $Zip = Join-Path $env:TEMP "python-$Version-embed-amd64.zip"
    Invoke-WebRequest "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip" -OutFile $Zip
    Expand-Archive -LiteralPath $Zip -DestinationPath $Runtime -Force
    $Pth = Join-Path $Runtime "python312._pth"
    (Get-Content $Pth) -replace '#import site','import site' | Set-Content $Pth -Encoding ASCII
    Add-Content $Pth "Lib\site-packages" -Encoding ASCII
    New-Item -ItemType Directory -Force -Path (Join-Path $Runtime "Lib\site-packages") | Out-Null
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $Runtime "get-pip.py")
    & $Python (Join-Path $Runtime "get-pip.py") --disable-pip-version-check
    & $Python -m pip install --disable-pip-version-check --no-warn-script-location -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    Set-Content $Ready "ready" -Encoding ASCII
}
$env:DATABASE_URL = "sqlite:///./data/decades-v4.db"
Start-Process "http://127.0.0.1:8000"
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
