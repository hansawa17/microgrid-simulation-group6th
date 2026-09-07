$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv first, reopen PowerShell, then rerun this script."
}

Push-Location $ProjectRoot
try {
    uv python install 3.11
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        uv venv --python 3.11 .venv
    }

    $Version = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($Version -ne "3.11") {
        throw ".venv uses Python $Version; remove or rename .venv, then rerun bootstrap.ps1."
    }

    uv pip install --python $VenvPython -r $Requirements
    & $VenvPython -c "from PyQt6.QtCore import PYQT_VERSION_STR; print(f'Environment ready: Python 3.11, PyQt6 {PYQT_VERSION_STR}')"
}
finally {
    Pop-Location
}

