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
    $VenvHealthy = $false
    if (Test-Path -LiteralPath $VenvPython) {
        try {
            $DetectedVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
            $VenvHealthy = ($LASTEXITCODE -eq 0 -and $DetectedVersion -eq "3.11")
        }
        catch {
            $VenvHealthy = $false
        }
    }
    if (-not $VenvHealthy) {
        $ExistingVenv = Join-Path $ProjectRoot ".venv"
        if (Test-Path -LiteralPath $ExistingVenv) {
            $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $BackupVenv = Join-Path $ProjectRoot ".venv.stale-$Stamp"
            Move-Item -LiteralPath $ExistingVenv -Destination $BackupVenv
            Write-Host "Preserved unusable environment at $BackupVenv"
        }
        uv venv --python 3.11 .venv
    }

    $Version = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($Version -ne "3.11") {
        throw ".venv uses Python $Version; expected Python 3.11."
    }

    uv pip install --python $VenvPython -r $Requirements
    & $VenvPython -c "from PyQt6.QtCore import PYQT_VERSION_STR; print(f'Environment ready: Python 3.11, PyQt6 {PYQT_VERSION_STR}')"
}
finally {
    Pop-Location
}

