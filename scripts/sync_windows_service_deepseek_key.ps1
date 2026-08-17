param(
    [string]$ServiceName = "CreditToolsPortal",
    [string]$ApiKey = ""
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated PowerShell session."
    }
}

Assert-Admin

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Nssm = Join-Path $ProjectDir "nssm.exe"
if (-not (Test-Path $Nssm)) {
    $Nssm = "nssm.exe"
}

if (-not $ApiKey) {
    $ApiKey = $env:DEEPSEEK_API_KEY
}
if (-not $ApiKey) {
    $ApiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
}
if (-not $ApiKey) {
    throw "DEEPSEEK_API_KEY was not found. Set it for the current user or pass -ApiKey."
}

# Preserve existing service variables and replace only the DeepSeek key.
$existing = @(& $Nssm get $ServiceName AppEnvironmentExtra 2>$null |
    ForEach-Object { $_.ToString().Trim() })
$updated = @($existing | Where-Object { $_ -and $_ -notmatch "^DEEPSEEK_API_KEY=" })
$updated += "DEEPSEEK_API_KEY=$ApiKey"

& $Nssm set $ServiceName AppEnvironmentExtra $updated
if ($LASTEXITCODE -ne 0) {
    throw "Unable to update NSSM service environment variables."
}
& $Nssm restart $ServiceName
if ($LASTEXITCODE -ne 0) {
    throw "Unable to restart the service."
}

Write-Host "DeepSeek key configured and $ServiceName restarted." -ForegroundColor Green
