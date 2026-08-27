param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating the isolated Python environment..."
    & $PythonCommand -m venv $VenvDir
}

Write-Host "Installing project dependencies..."
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")
Write-Host "Initialization complete. Run start_bond_trading.bat to launch." -ForegroundColor Green
