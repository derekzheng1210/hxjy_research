# -*- coding: utf-8 -*-
"""
每日一级发行结果汇总海报 · 自动生成编排
================================================================
- 输入：D:/2026/一级投标/投标情况/一级发行-信用债发行 YYYY-MM-DD.xlsx（需含「票面利率」列 = 收盘后版本；
  仅含投标区间的新券预告版会跳过，避免出无票面海报）
- 引擎：primary-issuance-results-poster 技能 assets/generate_poster.py（酒红版式 v2026-08-13 标准；
  内部统计/图表 + playwright 截图 PNG），venv: python/envs/poster
- 输出：<项目>/data/posters/每日一级发行结果汇总_YYYY-MM-DD.{html,png}
- 用法: python scripts/build_daily_poster.py [YYYY-MM-DD]   （缺省=目录中最新含票面的日期）
  退出码 0=已生成，1=无可生成数据（正常跳过），2=异常
"""
import glob, json, os, re, subprocess, sys, datetime as dt

EXCEL_DIR = r"D:/2026/一级投标/投标情况"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "posters")
SKILL_PY = r"C:/Users/yoyo1/.workbuddy/skills/primary-issuance-results-poster/assets/generate_poster.py"
POSTER_PY = r"C:/Users/yoyo1/.workbuddy/binaries/python/envs/poster/Scripts/python.exe"
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def list_daily_xlsx():
    if not os.path.isdir(EXCEL_DIR):
        return []
    out = []
    for fn in os.listdir(EXCEL_DIR):
        m = DATE_RE.search(fn)
        if m and fn.endswith(".xlsx") and "~$" not in fn and "登记表" not in fn:
            out.append((m.group(1), os.path.join(EXCEL_DIR, fn)))
    return sorted(out, key=lambda x: x[0])


def locate_poster_sheet(path):
    """定位「收盘版」明细 sheet：表头行需含「票面利率」（引擎按第 1 行解析）。
    返回 (sheet_title, header_row) 或 None。盘中/带标题行文件会被判为 not ready。
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                for hr in range(1, 4):
                    row = [str(c).strip() if c is not None else "" for c in
                           next(ws.iter_rows(min_row=hr, max_row=hr, values_only=True), ())]
                    if any("票面利率" in c or c == "票面" for c in row):
                        has_ref = any(any(k in c for k in ("债券简称", "债券代码", "发行人", "计划发行", "发行期限"))
                                      for c in row)
                        if has_ref:
                            return (ws.title, hr)
        finally:
            wb.close()
    except Exception:
        return None
    return None


def has_coupon_col(path):
    """收盘版判定：表头在第 1 行且含票面利率（引擎 find_col_by_header 只扫 row1）"""
    hit = locate_poster_sheet(path)
    if not hit:
        return False
    return hit[1] == 1


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    want = argv[0] if argv else None
    files = list_daily_xlsx()
    if not files:
        print(json.dumps({"ok": False, "reason": "no_excel_dir"}, ensure_ascii=False))
        return 2

    # 选定目标文件：指定日期精确匹配；缺省取最新
    target = None
    if want:
        for d, p in files:
            if d == want:
                target = (d, p)
                break
        if target is None:
            print(json.dumps({"ok": False, "reason": "date_not_found", "date": want}, ensure_ascii=False))
            return 2
    else:
        target = files[-1]

    date, path = target
    if not has_coupon_col(path):
        print(json.dumps({
            "ok": False, "reason": "no_coupon_col",
            "date": date, "file": os.path.basename(path),
            "message": f"{date} 的 Excel 未含「票面利率」列（收盘版尚未生成），跳过海报。",
        }, ensure_ascii=False))
        return 1

    if not (os.path.exists(SKILL_PY) and os.path.exists(POSTER_PY)):
        print(json.dumps({"ok": False, "reason": "engine_missing",
                          "skill": os.path.exists(SKILL_PY), "py": os.path.exists(POSTER_PY)}, ensure_ascii=False))
        return 2

    os.makedirs(OUT_DIR, exist_ok=True)
    cmd = [POSTER_PY, SKILL_PY, "--excel", path, "--date", date, "--out-dir", OUT_DIR]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except Exception as e:
        print(json.dumps({"ok": False, "reason": "engine_error", "error": str(e)}, ensure_ascii=False))
        return 2

    png = os.path.join(OUT_DIR, f"每日一级发行结果汇总_{date}.png")
    html = os.path.join(OUT_DIR, f"每日一级发行结果汇总_{date}.html")
    made = os.path.exists(png) and os.path.getsize(png) > 1000

    if r.returncode != 0 or not made:
        print(json.dumps({"ok": False, "reason": "engine_fail", "date": date,
                          "rc": r.returncode,
                          "stderrTail": (r.stderr or "").strip().splitlines()[-8:],
                          "stdoutTail": (r.stdout or "").strip().splitlines()[-8:]}, ensure_ascii=False))
        return 2

    print(json.dumps({
        "ok": True, "date": date,
        "file": os.path.basename(path),
        "pngSize": os.path.getsize(png),
        "posterDir": os.path.normpath(OUT_DIR),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
