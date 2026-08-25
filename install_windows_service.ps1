param(
    [string]$ServiceName = "CreditToolsPortal",
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
# 运态数据根目录：环境变量优先，否则默认项目同级 juyuan_credit_data
$DataRoot = $env:PORTAL_DATA_ROOT
if (-not $DataRoot) { $DataRoot = Join-Path (Split-Path -Parent $ProjectDir) "juyuan_credit_data" }
$LogDir = Join-Path $DataRoot "logs"
$NssmCandidates = @(
    (Join-Path $ProjectDir "nssm.exe"),
    (Join-Path $ProjectDir "tools\nssm.exe"),
    (Join-Path $ProjectDir "tools\nssm\win64\nssm.exe"),
    (Join-Path $ProjectDir "tools\nssm-2.24\win64\nssm.exe"),
    "nssm.exe"
)

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run PowerShell as Administrator, then run this script again."
    }
}

function Find-Nssm {
    foreach ($candidate in $NssmCandidates) {
        try {
            $cmd = Get-Command $candidate -ErrorAction Stop
            if ($cmd.Source) { return $cmd.Source }
        } catch {
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

Assert-Admin

$Nssm = Find-Nssm
if (-not $Nssm) {
    Write-Host ""
    Write-Host "nssm.exe was not found. Download NSSM, then copy win64\nssm.exe to this project folder:" -ForegroundColor Yellow
    Write-Host $ProjectDir
    Write-Host ""
    Write-Host "Download: https://nssm.cc/download"
    Write-Host "After copying nssm.exe, run this script again."
    exit 1
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Python virtual environment .venv ..."
    python -m venv (Join-Path $ProjectDir ".venv")
}

Write-Host "Installing Python dependencies ..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")

$SitePassword = Read-Host "Enter SITE_PASSWORD for website login"
$SecretKey = Read-Host "Enter Flask SECRET_KEY, use a long random string"
# 知识搜索使用服务进程自身的环境变量。不能只设置到当前用户环境，
# 否则 NSSM 服务（尤其以其他账户运行时）不会继承该密钥。
$DeepSeekApiKey = $env:DEEPSEEK_API_KEY
if (-not $DeepSeekApiKey) {
    $DeepSeekApiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
}
if (-not $DeepSeekApiKey) {
    $DeepSeekApiKey = Read-Host "Enter DEEPSEEK_API_KEY (leave blank to disable knowledge search)"
}

Write-Host "Registering Windows service $ServiceName ..."
$ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($ExistingService) {
    Write-Host "Existing service found. Removing old service first ..."
    & $Nssm stop $ServiceName | Out-Null
    & $Nssm remove $ServiceName confirm | Out-Null
}

# 以 waitress 生产入口运行（gunicorn 不支持 Windows；app.py 仅用于本地开发）
& $Nssm install $ServiceName $VenvPython "run_production.py"
& $Nssm set $ServiceName AppDirectory $ProjectDir
$ServiceEnvironment = @(
    "PORTAL_DATA_ROOT=$DataRoot",
    "SITE_PASSWORD=$SitePassword",
    "SECRET_KEY=$SecretKey",
    "FLASK_DEBUG=0",
    "PORT=$Port"
)
if ($DeepSeekApiKey) {
    $ServiceEnvironment += "DEEPSEEK_API_KEY=$DeepSeekApiKey"
}
& $Nssm set $ServiceName AppEnvironmentExtra $ServiceEnvironment
& $Nssm set $ServiceName AppStdout (Join-Path $LogDir "service-out.log")
& $Nssm set $ServiceName AppStderr (Join-Path $LogDir "service-error.log")
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateOnline 1
& $Nssm set $ServiceName Start SERVICE_AUTO_START
& $Nssm start $ServiceName

Write-Host ""
Write-Host "Done. The service is running and will auto-start after reboot." -ForegroundColor Green
Write-Host "Open on this computer: http://127.0.0.1:$Port"
Write-Host "Open from coworkers' computers: http://YOUR-INTRANET-IP:$Port"
Write-Host ""
Write-Host "Check status: nssm status $ServiceName"
Write-Host "Stop service: nssm stop $ServiceName"
Write-Host "Start service: nssm start $ServiceName"
