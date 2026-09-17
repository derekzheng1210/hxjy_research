"""一次性运维脚本：强制应用 Oracle 债券池并重建择券估值缓存。

走 oracle_pool_apply.start_apply 的正式路径（与管理页按钮一致），
运行时状态写入 oracle_pool_apply_status.json。
"""
import sys
import time

sys.path.insert(0, ".")

from juyuan_update.oracle_pool_apply import load_status, start_apply

ok, message = start_apply()
print("started:", ok, message, flush=True)
if not ok:
    sys.exit(1)
while True:
    status = load_status()
    if not status.get("running"):
        print("finished ok=", status.get("ok"), "error=", status.get("error"), flush=True)
        print("\n".join(status.get("log") or []), flush=True)
        sys.exit(0 if status.get("ok") else 2)
    print("...", (status.get("log") or ["进行中"])[-1][:150], flush=True)
    time.sleep(10)
