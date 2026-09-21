# -*- coding: utf-8 -*-
"""
推荐个券综合修复 v3：
1. 精确定位"票面"/"票面-预测"/"新债预测"列（前缀匹配，排除列名互相干扰）
   - 08-17 版式：票面-预测 排在 票面利率(%) 之前 → 旧 find_col(["票面"]) 会把 coupon 误读为 bp 值
2. coupon 重读修复（26浙江新能MTN001/002 的 coupon 误存 -3.0 → 应为 1.59）
3. 票面-预测(sp_bp) 补全：优先 Excel"票面-预测"列；缺失时用 (coupon - forecast) * 100 计算
4. coupon_raw（回拨/取消标记）保持：仅 Excel 票面原文含"回拨"/"取消"时更新；#VALUE! 不动现有值
"""
import openpyxl, os, json, re

BASE = "D:/2026/一级投标/投标情况"
REC_PATH = "C:/Users/yoyo1/WorkBuddy/2026-09-01-16-51-51/bond-dashboard/data/recommended.json"

rec = json.load(open(REC_PATH, encoding="utf-8"))
by_date = {}
for b in rec:
    by_date.setdefault(b["date"], []).append(b)

def find_col(ws, pattern, max_col=40):
    for r in range(1, 4):
        for c in range(1, max_col + 1):
            v = ws.cell(r, c).value
            if v and re.match(pattern, str(v).strip()):
                return c
    return None

def to_num(v):
    if v is None:
        return None
    s = str(v).replace("\u00a0", " ").replace("\u3000", " ").strip().strip("'\"")
    if s in ("", "--", "-", "None", "N/A", "#N/A"):
        return None
    try:
        return float(s.replace(",", "").replace("%", ""))
    except Exception:
        return None

files = [f for f in os.listdir(BASE) if re.match(r"一级发行-信用债发行 2026-\d{2}-\d{2}\.xlsx$", f)]
coupon_fixed = sp_filled = 0

for fn in files:
    d = fn[11:21]
    recs = by_date.get(d)
    if not recs:
        continue
    wb = openpyxl.load_workbook(os.path.join(BASE, fn), data_only=True)
    ws = wb.worksheets[0]
    cn = find_col(ws, r"^债券简称")
    # 票面列：以"票面"开头但排除"票面-预测"
    cc = None
    for r in range(1, 4):
        for c in range(1, min(ws.max_column, 40) + 1):
            v = ws.cell(r, c).value
            if v:
                s = str(v).strip()
                if re.match(r"^票面", s) and not s.startswith("票面-预测"):
                    cc = c
                    break
        if cc:
            break
    csp = find_col(ws, r"^票面-预测")
    cf = find_col(ws, r"^新债预测")
    if not cn or not cc:
        continue
    m = {b["name"]: b for b in recs}
    for r in range(2, ws.max_row + 1):
        nm = ws.cell(r, cn).value
        if not nm or str(nm).strip() not in m:
            continue
        b = m[str(nm).strip()]
        cv_raw = ws.cell(r, cc).value
        cv_num = to_num(cv_raw)
        # 1) coupon 修复
        old_coupon = b.get("coupon")
        if cv_num is not None:
            if old_coupon != cv_num:
                b["coupon"] = cv_num
                coupon_fixed += 1
            # 正常定价 → 清除历史 coupon_raw（除非 Excel 原文有标记）
            raw_s = str(cv_raw).strip() if cv_raw is not None else ""
            if raw_s and ("回拨" in raw_s or "取消" in raw_s):
                b["coupon_raw"] = raw_s
            elif b.get("coupon_raw") and not ("回拨" in b["coupon_raw"] or "取消" in b["coupon_raw"]):
                # 之前是 #VALUE! 等异常 → 现在有数字票面则清除
                b.pop("coupon_raw", None)
        # 2) 票面-预测：Excel 列有值优先，否则减法补全
        excel_sp = to_num(ws.cell(r, csp).value) if csp else None
        calc_sp = None
        if b.get("coupon") is not None and b.get("forecast") is not None:
            calc_sp = round((b["coupon"] - b["forecast"]) * 100, 1)
        new_sp = excel_sp if excel_sp is not None else calc_sp
        if new_sp is not None:
            old_sp = b.get("sp_bp")
            # Excel 的 sp_bp 若与减法算出偏差>10bp 且 |excel|<10，说明 excel 是 bp 值直接用；保留 excel
            if old_sp != new_sp:
                b["sp_bp"] = new_sp
                if old_sp is None:
                    sp_filled += 1

with open(REC_PATH, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False, indent=1)

# 统计
noSp = [b for b in rec if b.get("sp_bp") is None]
badCoupon = [b for b in rec if isinstance(b.get("coupon"), (int, float)) and (b["coupon"] < 0 or b["coupon"] > 10)]
print(f"coupon 修复/更新: {coupon_fixed}；sp_bp 补全: {sp_filled}")
print(f"仍无票面-预测: {len(noSp)}/{len(rec)}")
print(f"coupon 异常(负或>10): {len(badCoupon)}")
for b in badCoupon:
    print(f"  {b['name']} coupon={b['coupon']}")
# 校验
for n in ["26浙江新能MTN001(并购)", "26浙江新能MTN002(并购)", "26赣铁04", "26沪盛K1"]:
    b = rec.find(lambda x: False) if False else next((x for x in rec if x["name"] == n), None)
    if b:
        print(f"校验 {n}: coupon={b['coupon']} forecast={b['forecast']} sp_bp={b['sp_bp']} coupon_raw={b.get('coupon_raw')}")
