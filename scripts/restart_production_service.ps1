# 以管理员身份重启生产服务 CreditToolsPortal
# 用法（普通权限运行即可，会弹 UAC 确认框）：
#   powershell -ExecutionPolicy Bypass -File scripts\restart_production_service.ps1
try {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
        '-NoProfile', '-Command',
        'Restart-Service -Name CreditToolsPortal -Force; Start-Sleep -Seconds 3'
    )
    Start-Sleep -Seconds 2
    $status = (Get-Service CreditToolsPortal).Status
    Write-Output ("SERVICE-STATUS: " + $status)
} catch {
    Write-Output ("ELEVATE-FAILED: " + $_.Exception.Message)
}
