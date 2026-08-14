param(
    [string]$ServiceName = "CreditToolsPortal"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$NssmCandidates = @(
    (Join-Path $ProjectDir "nssm.exe"),
    (Join-Path $ProjectDir "tools\nssm.exe"),
    (Join-Path $ProjectDir "tools\nssm\win64\nssm.exe"),
    (Join-Path $ProjectDir "tools\nssm-2.24\win64\nssm.exe"),
    "nssm.exe"
)

foreach ($candidate in $NssmCandidates) {
    try {
        $cmd = Get-Command $candidate -ErrorAction Stop
        $Nssm = $cmd.Source
        break
    } catch {
        if (Test-Path $candidate) {
            $Nssm = $candidate
            break
        }
    }
}

if (-not $Nssm) {
    throw "nssm.exe was not found, cannot remove the service."
}

& $Nssm stop $ServiceName 2>$null | Out-Null
& $Nssm remove $ServiceName confirm

Write-Host "Service $ServiceName has been removed."
