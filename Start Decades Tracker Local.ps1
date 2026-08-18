$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv $venv
    } else {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $systemPython) { throw "Python 3 is required. Install it from python.org, then run this file again." }
        & $systemPython.Source -m venv $venv
    }
    & $python -m pip install --upgrade pip
    & $python -m pip install -r (Join-Path $root "requirements-local.txt")
}

$env:DECADES_STORAGE_MODE = "local"
Set-Location -LiteralPath $root
& $python -m streamlit run app.py --server.headless true
