param(
    [Alias("Bind")]
    [string]$BindAddress = "0.0.0.0",

    [ValidateRange(1, 65535)]
    [int]$Port = 5000,

    [string]$Db = "data\runtime\grid.db",
    [string]$Config = "A_simulator\config.example.json",
    [string]$Scenario = "A_simulator\scenarios\antarctic_10min.csv",

    [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BootstrapScript = Join-Path $PSScriptRoot "bootstrap.ps1"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Get-ProjectPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
}

function Invoke-Bootstrap {
    if ($SkipBootstrap) {
        throw "The project Python environment is not ready and -SkipBootstrap was specified."
    }
    if (-not (Test-Path -LiteralPath $BootstrapScript -PathType Leaf)) {
        throw "Bootstrap script not found: $BootstrapScript"
    }

    Write-Host "Preparing the project Python 3.11 environment..."
    & $BootstrapScript
    if ($LASTEXITCODE -ne 0) {
        throw "bootstrap.ps1 failed with exit code $LASTEXITCODE."
    }
}

$DbPath = Get-ProjectPath $Db
$ConfigPath = Get-ProjectPath $Config
$ScenarioPath = Get-ProjectPath $Scenario

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Configuration file not found: $ConfigPath"
}
if (-not (Test-Path -LiteralPath $ScenarioPath -PathType Leaf)) {
    throw "Scenario CSV not found: $ScenarioPath"
}
if ($PSBoundParameters.ContainsKey("BindAddress") -and [string]::IsNullOrWhiteSpace($BindAddress)) {
    throw "-BindAddress cannot be empty."
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Invoke-Bootstrap
}
else {
    & $VenvPython -c "import sys, PyQt6; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Bootstrap
    }
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "Project Python was not created: $VenvPython"
}

$DbDirectory = Split-Path -Parent $DbPath
if (-not (Test-Path -LiteralPath $DbDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $DbDirectory -Force | Out-Null
}

Push-Location $ProjectRoot
try {
    if (Test-Path -LiteralPath $DbPath -PathType Leaf) {
        Write-Host "Using existing database without reinitializing it: $DbPath"
    }
    else {
        Write-Host "Database is missing; creating it once: $DbPath"
        & $VenvPython -m A_simulator init --db $DbPath --config $ConfigPath --scenario $ScenarioPath
        if ($LASTEXITCODE -ne 0) {
            throw "A database initialization failed with exit code $LASTEXITCODE."
        }
    }

    $GuiArguments = @(
        "-m", "A_simulator", "gui",
        "--db", $DbPath,
        "--config", $ConfigPath,
        "--scenario", $ScenarioPath
    )
    if ($PSBoundParameters.ContainsKey("BindAddress")) {
        $GuiArguments += @("--bind", $BindAddress)
    }
    if ($PSBoundParameters.ContainsKey("Port")) {
        $GuiArguments += @("--port", $Port.ToString())
    }

    Write-Host "Starting A GUI..."
    & $VenvPython @GuiArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
