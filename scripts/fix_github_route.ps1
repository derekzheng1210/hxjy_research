# 临时把 github.com 固定到可用IP（本机防火墙只拦截DNS默认返回的20.205.243.166）
# 移除方法：删除 hosts 中带 hxjy-github-route 标记的行
$hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
if (-not (Select-String -Path $hosts -Pattern "github\.com" -Quiet)) {
    Add-Content -Path $hosts -Value "`n140.82.112.3 github.com # hxjy-github-route-20260904"
    Write-Host "hosts-entry-added"
} else {
    Write-Host "hosts-entry-exists"
}
ipconfig /flushdns | Out-Null
Write-Host "dns-flushed"
