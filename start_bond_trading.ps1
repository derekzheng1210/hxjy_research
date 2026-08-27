param(
    [int]$Port = 5011
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataRoot = Join-Path (Split-Path -Parent $ProjectDir) "juyuan_credit_bond_trading_data"
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "The isolated Python environment is missing. Run init_bond_trading.ps1 first."
}

$env:PORTAL_DATA_ROOT = $DataRoot
$env:PORT = [string]$Port
$env:HOST = "127.0.0.1"
$env:FLASK_DEBUG = "0"
$env:BROKER_SCHEDULER_ENABLED = "1"

Set-Location -LiteralPath $ProjectDir
Write-Host "Bond trading selector starting at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Closing this window also stops the site and intraday scheduler."
& $VenvPython "app.py"
