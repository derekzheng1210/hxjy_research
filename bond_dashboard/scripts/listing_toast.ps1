param(
  [string]$Title = "提醒",
  [string]$Body = ""
)
# Windows 桌面通知（NotifyIcon 气泡）——无需 AUMID/浏览器/打包。
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -STA -File listing_toast.ps1 -Title "xx" -Body "xx"
$ErrorActionPreference = "Stop"
try {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing

  $icon = New-Object System.Windows.Forms.NotifyIcon
  $icon.Icon = [System.Drawing.SystemIcons]::Information
  $icon.Visible = $true
  $icon.BalloonTipTitle = $Title
  $icon.BalloonTipText = $Body
  $icon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
  # 最多展示 12 秒
  $icon.ShowBalloonTip(12000)
  # 保持进程存活以保证气泡显示
  Start-Sleep -Milliseconds 1600
  $icon.Visible = $false
  $icon.Dispose()
} catch {
  # 静默失败（无桌面会话等场景不阻断调用方）
  exit 1
}
exit 0
