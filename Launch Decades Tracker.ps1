$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir

$RuntimeDir = Join-Path $AppDir ".runtime"
$PythonExe = Join-Path $RuntimeDir "python.exe"
$BootstrapMarker = Join-Path $RuntimeDir ".decades-ready"
$PythonVersion = "3.12.10"
$PythonZip = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonZip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

function Write-Step($Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

if (-not (Test-Path $BootstrapMarker)) {
    Write-Step "Preparing the private Decades Tracker runtime"
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

    if (-not (Test-Path $PythonExe)) {
        $ZipPath = Join-Path $env:TEMP $PythonZip
        Write-Step "Downloading the private Python runtime"
        Invoke-WebRequest -Uri $PythonUrl -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath $RuntimeDir -Force
        Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
    }

    $Pth = Join-Path $RuntimeDir "python312._pth"
    if (Test-Path $Pth) {
        $Lines = Get-Content $Pth
        $Lines = $Lines | ForEach-Object {
            if ($_ -eq "#import site") { "import site" } else { $_ }
        }
        if (-not ($Lines -contains "Lib\site-packages")) {
            $Lines += "Lib\site-packages"
        }
        Set-Content -Path $Pth -Value $Lines -Encoding ASCII
    }

    $SitePackages = Join-Path $RuntimeDir "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null

    $GetPip = Join-Path $RuntimeDir "get-pip.py"
    if (-not (Test-Path $GetPip)) {
        Write-Step "Downloading pip"
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPip -UseBasicParsing
    }

    Write-Step "Installing Decades Tracker dependencies"
    & $PythonExe $GetPip --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed." }

    & $PythonExe -m pip install --disable-pip-version-check --no-warn-script-location -r (Join-Path $AppDir "requirements-portable.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    Set-Content -Path $BootstrapMarker -Value "ready" -Encoding ASCII
    Write-Step "Setup complete"
}

Write-Step "Starting Decades Tracker"
Write-Host "Your browser should open automatically."
Write-Host "Keep this window open while using the tracker."
Write-Host "Close this window when you are finished."
Write-Host ""

& $PythonExe -m streamlit run (Join-Path $AppDir "app.py") --server.headless false --browser.gatherUsageStats false
