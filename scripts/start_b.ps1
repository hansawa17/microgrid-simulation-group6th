$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python environment is missing. Run scripts\bootstrap.ps1 first."
}

Push-Location $ProjectRoot
try {
    & $VenvPython -m B_dispatch run
}
finally {
    Pop-Location
}
