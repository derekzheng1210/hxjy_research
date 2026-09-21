# -*- coding: utf-8 -*-
"""
打包「展示端数据快照」，用于把本机(A)维护的数据同步到公司内网服务器(B)。

模型：A 机 = 数据生产端（Excel/DM/刷新脚本所在）；B 机 = 只读展示端。
- 打包范围：data/ 下全部静态数据（excel_calendar、valuations、recommended、
  issuer_ytd、history_trends、yy_lookup、store/bids.json 等）。
- 排除：data/store/messages.json —— 留言在 B 机产生并持久化，不能被 A 的副本覆盖。
- 输出：deploy/bond-data-snapshot-YYYYMMDD.zip（可用任意方式拷到服务器）。

用法：python scripts/pack_snapshot.py
"""
import json
import os
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "data"))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "deploy"))
OUT = os.path.join(OUT_DIR, f"bond-data-snapshot-{datetime.now():%Y%m%d}.zip")

# 明确不打包的文件（B 机本地产生/运行时缓存）
EXCLUDE = {
    "store/messages.json",          # 信息交流栏留言（服务器端持久化）
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    files = []
    for dirpath, _dirs, names in os.walk(ROOT):
        for n in names:
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel in EXCLUDE:
                continue
            if n.endswith(".tmp"):
                continue
            files.append((p, rel))
    files.sort(key=lambda x: x[1])

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p, rel in files:
            z.write(p, "data/" + rel)
    total_kb = sum(os.path.getsize(p) for p, _ in files) / 1024
    print(f"已打包 {len(files)} 个文件 → {OUT}（约 {total_kb:.0f} KB）")
    print("排除：", ", ".join(sorted(EXCLUDE)))


if __name__ == "__main__":
    main()
