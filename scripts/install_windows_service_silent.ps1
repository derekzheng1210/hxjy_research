param(
    [string]$ServiceName = "CreditToolsPortal",
    [string]$PythonExe = "D:\Python312\python.exe",
    [int]$Port = 5000
)

# 非交互式 NSSM 服务安装：以 waitress 生产入口 run_production.py 运行门户。
# 需要管理员权限（会由调用方以 UAC 提权运行）。

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Nssm = Join-Path $ProjectDir "nssm.exe"
if (-not (Test-Path $Nssm)) { throw "nssm.exe not found: $Nssm" }
if (-not (Test-Path $PythonExe)) { throw "Python not found: $PythonExe" }

$DataRoot = $env:PORTAL_DATA_ROOT
if (-not $DataRoot) { $DataRoot = Join-Path (Split-Path -Parent $ProjectDir) "juyuan_credit_data" }
$LogDir = Join-Path $DataRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# 回收旧的同名服务（如存在）
$Existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($Existing) {
    & $Nssm stop $ServiceName 2>$null | Out-Null
    Start-Sleep -Seconds 2
    & $Nssm remove $ServiceName confirm | Out-Null
}

& $Nssm install $ServiceName $PythonExe "run_production.py"
& $Nssm set $ServiceName AppDirectory $ProjectDir
& $Nssm set $ServiceName AppEnvironmentExtra @(
    "PORT=$Port",
    "FLASK_DEBUG=0",
    "PORTAL_DATA_ROOT=$DataRoot"
) | Out-Null
& $Nssm set $ServiceName AppStdout (Join-Path $LogDir "service-out.log")
& $Nssm set $ServiceName AppStderr (Join-Path $LogDir "service-error.log")
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateOnline 1
& $Nssm set $ServiceName AppStopMethodConsole 10
& $Nssm set $ServiceName Start SERVICE_AUTO_START
& $Nssm start $ServiceName | Out-Null

Start-Sleep -Seconds 3
$status = & $Nssm status $ServiceName
Write-Output "SERVICE_STATUS=$status"
