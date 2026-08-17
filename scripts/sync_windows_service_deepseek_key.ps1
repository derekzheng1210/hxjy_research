param(
    [string]$ServiceName = "CreditToolsPortal",
    [string]$ApiKey = ""
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "请使用‘以管理员身份运行’的 PowerShell 执行此脚本。"
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
    throw "未找到 DEEPSEEK_API_KEY。请先设置当前用户环境变量，或以 -ApiKey 参数传入。"
}

# 保留已配置的站点密码、端口等变量，仅替换 DeepSeek 密钥。
$existing = @(& $Nssm get $ServiceName AppEnvironmentExtra 2>$null |
    ForEach-Object { $_.ToString().Trim() })
$updated = @($existing | Where-Object { $_ -and $_ -notmatch "^DEEPSEEK_API_KEY=" })
$updated += "DEEPSEEK_API_KEY=$ApiKey"

& $Nssm set $ServiceName AppEnvironmentExtra $updated
if ($LASTEXITCODE -ne 0) {
    throw "无法更新 NSSM 服务环境变量。"
}
& $Nssm restart $ServiceName
if ($LASTEXITCODE -ne 0) {
    throw "无法重启服务。"
}

Write-Host "已为 $ServiceName 配置 DeepSeek 密钥并重启服务。" -ForegroundColor Green
