# -*- coding: utf-8 -*-
"""
每日全量数据刷新编排器（供 Windows 计划任务「每交易日 08:00」调用）
================================================================
依次执行 12 步，全部幂等/可重入：
  1. sync_excel_calendar.py  每日一级发行 Excel → data/excel_calendar.json + yy_lookup.json
     （仅当目录中出现比当前清单更新的交易日文件时才执行）
  2. update_recent.py        每日 Excel「是否推荐=是」→ data/recommended.json（脚本内部增量）
  3. sync_bids.py            一级中标登记表.xlsx → data/store/bids.json（幂等合并）
  4. fetch_valuations.py     DM 个券估值（含权取行权 yte）→ data/valuations.json
  5a. sync_issuer_ratings.py 发行人评级归集（YY 主表 + 外部评级缓存）→ data/cache/issuer_ratings.json
  5b. build_issuer_ytd.py    DM 一级发行(2026-01-01~最近交易日) → data/issuer_ytd.json
  6. update_history_csv.py   增量拉取历史一级发行 CSV（D:/DM API 实/raw_primary_history.csv）
  7. build_history_trends.py 历史趋势重算（仅当 CSV 比 json 新才执行）
  8. build_portfolio_series.py 中标组合时间序列 + 30Y 国债收益率（30Y 缓存增量；组合整段重算）
  9. build_coupon_curve.py    信用债一级发行票面-期限曲线（YY1-5 档，读历史 CSV 重算幂等）
 10. build_name_code.py      债券简称→security_id 映射（推荐清单代码兜底，读历史 CSV，剔除取消发行）
 11. build_bond_notes.py     票面缺失原因标注（取消发行/回拨X年/未截标）+ 票面回填（参与中标、推荐清单）
 12. sync_list_dates.py      参与/中标记录 DM 上市信息回填（代码/缴款日/上市日/发行人/票面），
                             供「中标个券上市提醒」板块使用（仅在缺失时写入，上市日以 DM 为准）
 13. build_issuer_metrics.py 发行人附加指标（交易所存量债券质押比区间 + 近五年 YY 评级调整），
                             读 issuer_ytd.json + 历史 CSV 交易所债券代码 → DM basic-info/company-rating

每步结果与关键数字写入 logs/daily_refresh_YYYYMMDD.log，
最新摘要写入 logs/last_summary.txt；--push 时推送到企业微信群机器人。
Webhook 从 bond-dashboard/.env.local 读取 WECHAT_WEBHOOK，缺失则回退内置默认。

用法:
  "<venv>/python.exe" scripts/daily_refresh.py [--push] [--steps 1,2,3,4,5,6,7,8,9,10,11,12] [--skip-excel-check]
"""
import json, os, re, subprocess, sys, datetime as dt

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LOGS = os.path.join(ROOT, "logs")
DATA = os.path.join(ROOT, "data")
EXCEL_DIR = r"D:/2026/一级投标/投标情况"
os.makedirs(LOGS, exist_ok=True)

TODAY = dt.date.today()
YESTERDAY = (TODAY - dt.timedelta(days=1)).isoformat()
LOG_FILE = os.path.join(LOGS, f"daily_refresh_{TODAY.isoformat()}.log")
SUMMARY_FILE = os.path.join(LOGS, "last_summary.txt")

# ---------------- 工具 ----------------
def log(msg):
    line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass  # pythonw 下无 stdout

def run_step(no, script, args=None, timeout=2400):
    args = args or []
    p = os.path.join(ROOT, "scripts", script)
    log(f"── 步骤{no} {script} {' '.join(args)} 开始")
    try:
        r = subprocess.run(
            [sys.executable, p] + args, cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        tail = (r.stdout or "").strip().splitlines()[-6:]
        for t in tail:
            log("    | " + t.strip())
        if r.returncode != 0:
            log(f"    ✗ 步骤{no} 失败 rc={r.returncode}")
            for t in (r.stderr or "").strip().splitlines()[-4:]:
                log("    ! " + t.strip())
            return False, r
        log(f"    ✓ 步骤{no} 完成")
        return True, r
    except subprocess.TimeoutExpired:
        log(f"    ✗ 步骤{no} 超时(>{timeout}s)")
        return False, None
    except Exception as e:
        log(f"    ✗ 步骤{no} 异常: {e}")
        return False, None

def jload(rel):
    try:
        with open(os.path.join(DATA, rel), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ---------------- 步骤1：Excel 是否有新增 ----------------
def excel_has_new():
    """目录中最新交易日文件 是否晚于 excel_calendar.json 已含最大日期"""
    cal = jload("excel_calendar.json") or {"days": {}}
    have = max((k for k in cal["days"].keys()), default="2000-01-01")
    latest = "2000-01-01"
    try:
        for fn in os.listdir(EXCEL_DIR):
            m = re.search(r"(20\d{2}-\d{2}-\d{2})", fn)
            if m and fn.endswith(".xlsx") and "~$" not in fn:
                latest = max(latest, m.group(1))
    except FileNotFoundError:
        return False, have, latest
    return latest > have, have, latest

# ---------------- 汇总统计 ----------------
def stats():
    s = {}
    cal = jload("excel_calendar.json") or {"days": {}}
    s["cal_days"] = len(cal.get("days", {}))
    s["cal_latest"] = max((k for k in cal.get("days", {}).keys()), default="-")
    s["cal_cnt"] = cal.get("days", {}).get(s["cal_latest"], {}).get("count", 0) if s["cal_latest"] != "-" else 0
    rec = jload("recommended.json")
    s["rec_cnt"] = len(rec) if isinstance(rec, list) else len(rec.get("list", []))
    bids = jload("store/bids.json")
    rows = bids if isinstance(bids, list) else bids.get("list", [])
    s["part_cnt"] = sum(1 for r in rows if r.get("type") == "participated")
    s["won_cnt"] = sum(1 for r in rows if r.get("type") == "won")
    # 上市提醒板块：已回填 DM 上市日的中标记录
    won_listed = [r for r in rows if r.get("type") == "won" and r.get("listDate")]
    s["won_listed"] = len(won_listed)
    s["won_list_eff"] = max((str(r.get("listDate")) for r in won_listed), default="-")
    val = jload("valuations.json") or {}
    vb = val.get("bonds", {})
    s["val_date"] = (val.get("meta") or {}).get("date", "-")
    s["cb_cnt"] = sum(1 for v in vb.values() if v.get("cb") is not None)
    s["cs_cnt"] = sum(1 for v in vb.values() if v.get("cs") is not None)
    iytd = jload("issuer_ytd.json") or {}
    s["iss_cn"] = (iytd.get("meta") or {}).get("issuerCount") or len(iytd.get("issuers", []))
    s["iss_end"] = ((iytd.get("meta") or {}).get("range") or "-").split("~")[-1]
    ps = jload("portfolio_series.json") or {}
    pts = ps.get("points", [])
    s["ps_cnt"] = len(pts)
    s["ps_end"] = pts[-1].get("date") if pts else "-"
    s["ps_dv01"] = pts[-1].get("dv01") if pts else None
    cc = jload("coupon_curve.json") or {}
    ccm = cc.get("meta", {})
    s["cc_cnt"] = ccm.get("bondCount")
    s["cc_end"] = (ccm.get("range") or "-").split("~")[-1]
    s["cc_coupon"] = ccm.get("couponAvg")
    return s

# ---------------- 企业微信推送 ----------------
def load_webhook():
    envf = os.path.join(ROOT, ".env.local")
    if os.path.exists(envf):
        for line in open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("WECHAT_WEBHOOK="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    # 回退默认：与南向通周报同一机器人（可在 .env.local 修改）
    return "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c307fab2-1846-4b14-9184-a20b4240e36e"

def push_wechat(text):
    import urllib.request
    body = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(load_webhook(), data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        return j.get("errcode") == 0, j
    except Exception as e:
        return False, {"error": str(e)}

def build_summary(results, skipped, have="-"):
    s = stats()
    L = []
    L.append(f"【债券一级看板】每日数据自动刷新完成 {TODAY.isoformat()}")
    if "1" in skipped:
        L.append(f"· Excel 总览清单：无新增文件，保持至 {have}（{s['cal_days']} 个交易日）")
    else:
        L.append(f"· Excel 总览清单：已更新至 {s['cal_latest']}，当日 {s['cal_cnt']} 只（累计 {s['cal_days']} 交易日）")
    L.append(f"· 推荐列表：{s['rec_cnt']} 只")
    L.append(f"· 参与/中标记录：参与 {s['part_cnt']} 条 / 中标 {s['won_cnt']} 条，已回填 DM 上市日 {s['won_listed']} 条（最近 {s['won_list_eff']}）")
    L.append(f"· 个券估值：估值日 {s['val_date']}，中债 {s['cb_cnt']} 只 / 中证 {s['cs_cnt']} 只（含权取行权）")
    L.append(f"· 发行人分析 YTD：{s['iss_cn']} 家（截至 {s['iss_end']}）")
    if s["ps_cnt"]:
        dv = f"，DV01 {s['ps_dv01']}万" if s["ps_dv01"] is not None else ""
        L.append(f"· 中标组合序列：{s['ps_cnt']} 点至 {s['ps_end']}{dv}（含 30Y 国债收益率）")
    if s.get("cc_cnt"):
        L.append(f"· 一级票面曲线：YY1-5 档 {s['cc_cnt']} 只，截至 {s['cc_end']}，加权票面 {s['cc_coupon']}%")
    imet = jload("cache/issuer_metrics.json") or {}
    imeta = imet.get("meta") or {}
    if imeta.get("pledgeCovered") or imeta.get("yyUpCount") or imeta.get("yyDownCount"):
        L.append(f"· 发行人附加指标：质押比覆盖 {imeta.get('pledgeCovered')} 家，YY 调高 {imeta.get('yyUpCount')} 家 / 调低 {imeta.get('yyDownCount')} 家")
    if "7" in skipped:
        L.append("· 历史趋势：CSV 无新增，保持最新")
    fails = [n for n in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13") if n not in skipped and not results.get(n)]
    if fails:
        L.append("· ⚠ 失败步骤：" + ",".join("步骤" + f for f in fails) + "（详见刷新日志）")
    else:
        L.append("· 全部步骤成功 ✓")
    L.append(f"刷新日志：{LOG_FILE}")
    return "\n".join(L)

# ---------------- main ----------------
def main():
    argv = sys.argv[1:]
    do_push = "--push" in argv

    # 独立模式：推送最近一次摘要（不重新刷新），用于测试/补推
    if "--push-last" in argv:
        if not os.path.exists(SUMMARY_FILE):
            print("无摘要文件，请先运行一次刷新")
            sys.exit(1)
        text = open(SUMMARY_FILE, encoding="utf-8").read()
        push_ok, resp = push_wechat(text)
        print(f"企业微信推送: {'成功' if push_ok else '失败 ' + json.dumps(resp, ensure_ascii=False)}")
        sys.exit(0 if push_ok else 2)
    steps = []
    if "--steps" in argv:
        i = argv.index("--steps")
        steps = [x.strip() for x in argv[i + 1].split(",")]
    log("===== 每日数据刷新开始 " + TODAY.isoformat() + " =====")
    log("解释器: " + sys.executable)

    skipped = []
    ok = {}
    have_val = "-"

    # 步骤1：Excel → 日历（有条件）
    if steps and "1" not in steps:
        skipped.append("1")
    else:
        new_excel, have, latest = excel_has_new()
        have_val = have
        log(f"Excel 目录最新文件日期={latest}，清单已含至 {have}，需更新={new_excel}")
        if new_excel:
            ok["1"], _ = run_step(1, "sync_excel_calendar.py")
        else:
            skipped.append("1")
            ok["1"] = True
            log("步骤1 跳过（无新增 Excel）")
    # 步骤2：推荐（增量）
    if steps and "2" not in steps:
        skipped.append("2")
    else:
        ok["2"], _ = run_step(2, "update_recent.py")
    # 步骤3：参与/中标
    if steps and "3" not in steps:
        skipped.append("3")
    else:
        ok["3"], _ = run_step(3, "sync_bids.py")
    # 步骤4：估值
    if steps and "4" not in steps:
        skipped.append("4")
    else:
        ok["4"], _ = run_step(4, "fetch_valuations.py")
    # 步骤5：发行人评级同步（本地 YY 主表归集 + 外部评级缓存）→ 发行人 YTD（截至昨日/最近交易日）
    if steps and "5" not in steps:
        skipped.append("5")
    else:
        r5a, _ = run_step("5a", "sync_issuer_ratings.py", ["--dm"], timeout=900)
        r5b, _ = run_step("5b", "build_issuer_ytd.py", [YESTERDAY], timeout=3600)
        ok["5"] = r5a and r5b
    # 步骤6：增量更新历史一级发行 CSV（拉到最近交易日）
    if steps and "6" not in steps:
        skipped.append("6")
    else:
        ok["6"], _ = run_step(6, "update_history_csv.py", [YESTERDAY], timeout=1800)
    # 步骤7：历史趋势重算（仅当 CSV 比 json 新）
    if steps and "7" not in steps:
        skipped.append("7")
    else:
        csv_p = r"D:/DM API 实/raw_primary_history.csv"
        json_p = os.path.join(DATA, "history_trends.json")
        if os.path.exists(csv_p) and os.path.exists(json_p):
            if os.path.getmtime(csv_p) > os.path.getmtime(json_p):
                ok["7"], _ = run_step(7, "build_history_trends.py")
            else:
                skipped.append("7")
                log("步骤7 跳过（历史 CSV 无新增）")
        else:
            skipped.append("7")
            log("步骤7 跳过（历史 CSV 或 json 缺失）")
    # 步骤8：中标组合序列 + 30Y 国债收益率（读步骤3/4产物；30Y 缓存增量，组合整段重算）
    if steps and "8" not in steps:
        skipped.append("8")
    else:
        ok["8"], _ = run_step(8, "build_portfolio_series.py", timeout=900)
    # 步骤9：信用债一级票面-期限曲线（读 raw_primary_history.csv，重算幂等）
    if steps and "9" not in steps:
        skipped.append("9")
    else:
        ok["9"], _ = run_step(9, "build_coupon_curve.py", timeout=300)
    # 步骤10：名称→代码映射（供推荐清单代码兜底，须在步骤6更新 CSV 之后）
    if steps and "10" not in steps:
        skipped.append("10")
    else:
        ok["10"], _ = run_step(10, "build_name_code.py", timeout=300)
    # 步骤11：票面缺失原因标注 + 票面回填（须在步骤2/3 之后）
    if steps and "11" not in steps:
        skipped.append("11")
    else:
        ok["11"], _ = run_step(11, "build_bond_notes.py", timeout=600)
    # 步骤12：参与/中标记录 DM 上市信息回填（代码/缴款日/上市日/发行人/票面）
    # 说明：上市提醒板块依赖 listDate；DM 每日新增上市日，故须每日同步（2026-09-11 用户要求）
    if steps and "12" not in steps:
        skipped.append("12")
    else:
        ok["12"], _ = run_step(12, "sync_list_dates.py", timeout=900)
    # 步骤13：发行人附加指标（交易所存量债券质押比区间 + 近五年 YY 评级调整）
    # 依赖步骤5b(issuer_ytd.json)与步骤6(历史CSV交易所债券代码)；读 DM basic-info/company-rating，约5-10分钟
    if steps and "13" not in steps:
        skipped.append("13")
    else:
        ok["13"], _ = run_step(13, "build_issuer_metrics.py", timeout=1800)

    summary = build_summary(ok, skipped, have=have_val)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    log("──── 摘要 ────")
    for line in summary.splitlines():
        log(line)

    if do_push:
        push_ok, resp = push_wechat(summary)
        log(f"企业微信推送: {'成功' if push_ok else '失败 ' + json.dumps(resp, ensure_ascii=False)}")
        if not push_ok:
            # 重试一次
            import time
            time.sleep(2)
            push_ok, resp = push_wechat(summary)
            log(f"企业微信推送(重试): {'成功' if push_ok else '失败 ' + json.dumps(resp, ensure_ascii=False)}")
    log("===== 每日数据刷新结束 =====")

if __name__ == "__main__":
    main()
