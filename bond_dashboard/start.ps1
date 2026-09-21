# Bond Primary Dashboard · 通用分离式启动器（部署版，不依赖本机绝对路径）
# ---------------------------------------------------------------------------
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
# 说明：通过脚本自身所在目录推导项目根目录，node 从系统 PATH 解析；
#       实际启动由 scripts/serve.mjs 完成（spawn 分离进程，关终端不影响服务）。
# ---------------------------------------------------------------------------
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$LOG  = Join-Path $ROOT "server.log"

function Write-Log([string]$m) {
  try { "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m |
        Out-File -FilePath $LOG -Append -Encoding utf8 } catch {}
}

# 端口已在监听 → 跳过
$busy = $null
try { $busy = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue } catch {}
if ($busy) { Write-Log "port 3000 already listening, skip."; exit 0 }

# 解析 node.exe：优先 PATH 中的 node
$node = $null
$c = Get-Command node -ErrorAction SilentlyContinue
if ($c) { $node = $c.Source }
if (-not $node) {
  # 兜底：常见安装位置
  foreach ($p in @(
    "$env:ProgramFiles\nodejs\node.exe",
    "$env:ProgramFiles(x86)\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe"
  )) { if (Test-Path $p) { $node = $p; break } }
}
if (-not $node) { Write-Log "ERROR: node.exe not found. Please install Node.js (>=18) and retry."; exit 1 }

Write-Log "launching via $node"
& $node (Join-Path $ROOT "scripts\serve.mjs")
exit $LASTEXITCODE
