$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$CliArguments = @($args)

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Project environment is missing. Run scripts\bootstrap.ps1 first."
}

Push-Location $ProjectRoot
try {
    & $VenvPython -m A_simulator @CliArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

