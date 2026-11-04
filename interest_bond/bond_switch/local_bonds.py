"""30年地方债区域观察：同日估值、五交易日活跃度和同省配对。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import mean

from . import db, oracle_source

TAX_CUTOFF = "20250808"
POLICY_URL = "https://zhejiang.chinatax.gov.cn/art/2025/8/4/art_8409_84255.html"
GOOD = ("广东", "江苏", "浙江", "北京", "上海", "深圳")
BAD = ("黑龙江", "吉林", "辽宁", "云南", "贵州", "天津", "广西", "青海", "甘肃", "内蒙古")
PROVINCES = GOOD + BAD + ("河北", "山西", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "海南", "重庆", "四川", "西藏", "陕西", "宁夏", "新疆", "大连", "宁波", "厦门", "青岛")
GROUPS = (("good", "好地区", GOOD), ("middle", "中地区", tuple(p for p in PROVINCES if p not in GOOD + BAD)), ("bad", "差地区", BAD))
TABLES = (("active", "30年活跃地方债（新税券）"), ("exempt", "30年免税地方债"))


def province_of(name: str) -> str | None:
    # 计划单列市优先，深圳与广东等分别归类。
    return next((p for p in sorted(PROVINCES, key=lambda p: (p not in ("深圳", "大连", "宁波", "厦门", "青岛"), -len(p))) if p in name), None)


def _range(values):
    values = [float(v) for v in values if v is not None]
    return [round(min(values), 4), round(max(values), 4)] if values else None


def _avg(values):
    values = [float(v) for v in values if v is not None]
    return round(mean(values), 4) if values else None


def build_dashboard(bonds: list[dict], as_of: str, days: list[str], benchmark: dict | None) -> dict:
    selected = {}
    for province in PROVINCES:
        pool = [b for b in bonds if b.get("province") == province and 27 <= b["remaining_years"] <= 30.1 and b.get("valuation_yield") is not None]
        new = [b for b in pool if b.get("issue_date", "") >= TAX_CUTOFF]
        old = [b for b in pool if b.get("issue_date") and b["issue_date"] < TAX_CUTOFF]
        rank = lambda b: (b["avg_deals_5d"], b.get("outstanding_amount") or 0, b.get("volume_5d", 0), b["issue_date"], b["code"])
        active = max((b for b in new if b["avg_deals_5d"] > 0), key=rank, default=None)
        exempt = max((b for b in old if b["avg_deals_5d"] > 0), key=rank, default=None)
        selected[province] = {"active": active, "exempt": exempt}

    def spread(a, b):
        return round((a["valuation_yield"] - b["valuation_yield"]) * 100, 4) if a and b else None

    comparisons, tables = [], []
    for key, label, provinces in GROUPS:
        details = [{"province": p, **selected[p], "tax_bp": spread(selected[p]["active"], selected[p]["exempt"])} for p in provinces]
        tax = [r["tax_bp"] for r in details if r["tax_bp"] is not None]
        comparisons.append({"key": key, "label": label, "tax_bp": _avg(tax), "tax_pairs": len(tax), "details": details})
    for kind, title in TABLES:
        groups = []
        for key, label, provinces in GROUPS:
            details = [{"province": p, "bond": selected[p][kind]} for p in provinces]
            picks = [r["bond"] for r in details if r["bond"]]
            traded = [b for b in picks if b.get("deal_count", 0) > 0]
            trade_range = _range([b.get("trade_yield") for b in traded])
            valuation_range = _range([b["valuation_yield"] for b in picks])
            benchmark_yield = benchmark.get("valuation_yield") if benchmark else None
            groups.append({"key": key, "label": label, "details": details, "covered": len(picks), "total": len(provinces),
                           "trade_range": trade_range, "width_bp": round((valuation_range[1] - valuation_range[0]) * 100, 4) if valuation_range else None,
                           "vs_treasury_bp": [round((v - benchmark_yield) * 100, 4) for v in valuation_range] if valuation_range and benchmark_yield is not None else None,
                           "valuation_range": valuation_range,
                           "quote_anomalies": sum(b.get("trade_yield") is not None and abs(b["trade_yield"] - b["valuation_yield"]) > .2 for b in traded),
                           "valuation_yield": _avg([b["valuation_yield"] for b in picks]), "avg_deals_5d": _avg([b["avg_deals_5d"] for b in picks])})
        tables.append({"key": kind, "title": title, "groups": groups})
    return {"as_of_date": as_of, "trade_days": days, "benchmark": benchmark, "comparisons": comparisons, "tables": tables,
            "candidate_count": len(bonds), "unmapped_count": sum(not b.get("province") for b in bonds), "policy_url": POLICY_URL, "region_version": 3}


def refresh(db_path=None, progress=print) -> dict:
    """批量读取并原子替换快照；失败时保留上次成功数据。"""
    progress("更新30年地方债区域利差")
    with oracle_source.connect() as conn:
        # 盘中行情表持续写入，45天日历DISTINCT查询在交易时段可能超过2分钟。
        conn.call_timeout = 300000
        cur = conn.cursor()
        cur.arraysize = 5000
        # 行情表没有交易日期索引，批量取实际存在的日期，避免对空日期反复全表扫描。
        start = (date.today() - timedelta(days=45)).strftime('%Y%m%d')
        cur.execute("""SELECT TRADEDATE FROM (
            SELECT DISTINCT TRADEDATE FROM TQ_QT_BDIBQUOTE
            WHERE TRADEDATE BETWEEN :s AND :e AND ISVALID=1
            ORDER BY TRADEDATE DESC
        ) WHERE ROWNUM<=10""", s=start, e=date.today().strftime('%Y%m%d'))
        quote_days = [str(row[0]) for row in cur.fetchall()]
        days = []
        for day in quote_days:
            if not days:
                cur.execute("SELECT 1 FROM TQ_QT_CBESTIMATE WHERE TRADEDATE=:d AND DATASOURCE='1' AND ISVALID=1 AND ROWNUM=1", d=day)
                if not cur.fetchone():
                    continue
            days.append(day)
            if len(days) == 5:
                break
        days.sort()
        if len(days) != 5:
            raise RuntimeError("地方债近5个交易日成交数据不足")
        as_of = days[-1]
        progress(f"地方债估值与首次发行日 {as_of}")
        cur.execute("""
            WITH est AS (
              SELECT e.SECODE,e.YIELD,e.TERMTOMATURITY,e.MODDURATION,
                ROW_NUMBER() OVER(PARTITION BY e.SECODE ORDER BY CASE WHEN e.VALUATIONTYPE='1' THEN 0 ELSE 1 END,e.ID DESC) rn
              FROM TQ_QT_CBESTIMATE e
              WHERE e.TRADEDATE=:d AND e.DATASOURCE='1' AND e.ISVALID=1
                AND e.YIELD IS NOT NULL AND e.TERMTOMATURITY BETWEEN 27 AND 30.1
            )
            SELECT b.SYMBOL,b.BONDSNAME,b.BONDNAME,b.SECODE,b.ISSBEGDATE,
              e.YIELD,e.TERMTOMATURITY,e.MODDURATION,b.BONDTYPE2,
              COALESCE(NULLIF(b.CURRENTAMT,0),b.ACTISSAMT)
            FROM est e JOIN TQ_BD_NEWESTBASICINFO b ON b.SECODE=e.SECODE AND b.ISVALID=1
            WHERE e.rn=1 AND b.EXCHANGE='001005' AND b.BONDTYPE2 IN ('121','111')
              AND b.MATURITYYEAR=30
        """, d=as_of)
        bonds, treasuries = [], []
        for code, name, full, secode, issue, yld, remaining, duration, kind, amount in cur.fetchall():
            bond = {"code": str(code), "short_name": name or str(code), "full_name": full or name,
                    "secode": str(secode), "issue_date": str(issue)[:8] if issue else "", "valuation_yield": float(yld),
                    "remaining_years": float(remaining), "duration": float(duration) if duration is not None else None,
                    "outstanding_amount": float(amount) if amount is not None else None,
                    "province": province_of(full or name or ""), "avg_deals_5d": 0, "volume_5d": 0,
                    "deal_count": 0, "trade_yield": None}
            (bonds if str(kind) == '121' else treasuries).append(bond)
        by_secode = {b["secode"]: b for b in bonds + treasuries}
        # 一次批量聚合发行事件，避免逐券关联 MIN 扫描发行大表。
        progress(f"地方债批量核对首次发行日 {len(bonds)}只")
        secodes = list(by_secode)
        for offset in range(0, len(secodes), 900):
            batch = secodes[offset:offset + 900]
            placeholders = ','.join(f':s{i}' for i in range(len(batch)))
            cur.execute(f"SELECT SECODE,MIN(ISSBEGDATE) FROM TQ_BD_ISSUE WHERE ISVALID=1 AND SECODE IN ({placeholders}) GROUP BY SECODE", {f's{i}': s for i, s in enumerate(batch)})
            # 逐批消费：execute 会覆盖游标结果集，移出循环只剩最后一批。
            for secode, first_issue in cur:
                if first_issue:
                    b = by_secode[str(secode)]
                    b['issue_date'] = min(d for d in (b['issue_date'], str(first_issue)[:8]) if d)
        # 提前还本券（还本计划表有记录，如24河北债53每年还本5%）逐年摊还本金，
        # 久期与现金流结构和到期一次还本的30年券不可比，整体排除。
        amortizing = set()
        for offset in range(0, len(secodes), 900):
            batch = secodes[offset:offset + 900]
            placeholders = ','.join(f':s{i}' for i in range(len(batch)))
            cur.execute(f"SELECT DISTINCT SECODE FROM TQ_BD_CASHPRINCIPAL WHERE ISVALID=1 AND SECODE IN ({placeholders})", {f's{i}': s for i, s in enumerate(batch)})
            amortizing.update(str(row[0]) for row in cur)
        bonds = [b for b in bonds if b["secode"] not in amortizing]
        treasuries = [b for b in treasuries if b["secode"] not in amortizing]
        by_secode = {b["secode"]: b for b in bonds + treasuries}
        progress(f"地方债排除提前还本券 {len(amortizing)}只，剩 {len(bonds)}只候选券")
        progress(f"地方债近5日成交 {len(bonds)}只候选券")
        cur.execute("""SELECT SECODE,TRADEDATE,SUM(NVL(DEALS,0)),SUM(NVL(VOL,0)),
                       COALESCE(SUM(CASE WHEN AVGYIELD>0 THEN AVGYIELD*VOL END) /
                         NULLIF(SUM(CASE WHEN AVGYIELD>0 THEN VOL END),0), AVG(CASE WHEN AVGYIELD>0 THEN AVGYIELD END))
                       FROM TQ_QT_BDIBQUOTE WHERE TRADEDATE BETWEEN :s AND :e AND ISVALID=1
                       GROUP BY SECODE,TRADEDATE""", s=days[0], e=as_of)
        for secode, day, deals, volume, trade_yield in cur:
            b = by_secode.get(str(secode))
            if not b or str(day) not in days:
                continue
            b["avg_deals_5d"] += int(deals or 0) / 5
            b["volume_5d"] += float(volume or 0)
            if str(day) == as_of:
                b.update(deal_count=int(deals or 0), trade_yield=float(trade_yield) if trade_yield is not None else None)
    for b in bonds + treasuries:
        b["avg_deals_5d"] = round(b["avg_deals_5d"], 2)
    if not bonds:
        raise RuntimeError("未找到30年地方债估值候选券")
    benchmark = max(treasuries, key=lambda b: (b["avg_deals_5d"], b.get("outstanding_amount") or 0, b["volume_5d"], b["code"]), default=None)
    payload = build_dashboard(bonds, as_of, days, benchmark)
    with db.transaction(db_path) as conn:
        for key, value in (("local_dashboard", json.dumps(payload, ensure_ascii=False)), ("local_error", "")):
            conn.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    try:
        refresh_history(payload, db_path, progress)
        db.set_meta('local_history_error', '', db_path)
    except Exception as exc:
        # 历史补齐失败不回滚已经成功写入的最新快照。
        db.set_meta('local_history_error', str(exc)[:800], db_path)
        progress(f'地方债历史估值更新失败：{exc}')
    return payload


def dashboard(db_path=None, with_percentile: bool = False) -> dict:
    raw = db.get_meta("local_dashboard", db_path=db_path)
    payload = json.loads(raw) if raw else {"as_of_date": None, "tables": [], "comparisons": []}
    if raw and payload.get('region_version') != 3:
        # 旧快照立即按新区域分组重算，保留原始选券和数据日期。
        picks = {r['bond']['code']: r['bond'] for t in payload['tables'] for g in t['groups'] for r in g['details'] if r['bond']}
        grouped = build_dashboard(list(picks.values()), payload['as_of_date'], payload['trade_days'], payload.get('benchmark'))
        grouped['candidate_count'] = payload.get('candidate_count', len(picks))
        grouped['unmapped_count'] = payload.get('unmapped_count', 0)
        payload = grouped
    payload["update_error"] = db.get_meta("local_error", db_path=db_path)
    payload['history_error'] = db.get_meta('local_history_error', db_path=db_path)
    if with_percentile:
        _attach_percentiles(payload, db_path)
    return payload


def _attach_percentiles(payload: dict, db_path=None) -> None:
    """当前税差在过去一年逐日税差中的分位（含当日，末日即快照值）。"""
    as_of = payload.get('as_of_date')
    for g in payload['comparisons']:
        g['tax_percentile_1y'] = None
    if not as_of:
        return
    start = (date(int(as_of[:4]), int(as_of[4:6]), int(as_of[6:8])) - timedelta(days=366)).strftime('%Y%m%d')
    for g in payload['comparisons']:
        current = g.get('tax_bp')
        if current is None:
            continue
        _, values, _ = _tax_series(g['details'], start, as_of, db_path)
        history = [v for v in values if v is not None]
        if history:
            g['tax_percentile_1y'] = round(100.0 * sum(v <= current for v in history) / len(history), 1)


def refresh_history(payload: dict, db_path=None, progress=print) -> None:
    """当前选券回溯估值；独立缓存，避免混入国债候选池。"""
    end = payload['as_of_date']
    end_date = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    start = (end_date - timedelta(days=366)).strftime('%Y%m%d')
    picks = {r['bond']['code']: r['bond'] for t in payload['tables'] for g in t['groups'] for r in g['details'] if r['bond']}
    with db.connect(db_path) as conn:
        bounds = {r[0]: (r[1], r[2]) for r in conn.execute('SELECT code,MIN(trade_date),MAX(trade_date) FROM local_valuation_points GROUP BY code')}
    full, incremental = [], []
    for code, bond in picks.items():
        first_needed = max(start, bond.get('issue_date') or start)
        low, high = bounds.get(code, (None, None))
        # 发行后首个交易日不一定有估值，留7天容差，避免重复全量拉取。
        tolerance = (date(int(first_needed[:4]), int(first_needed[4:6]), int(first_needed[6:8])) + timedelta(days=7)).strftime('%Y%m%d')
        (full if not low or low > tolerance else incremental).append(code)
    rows = []
    if full:
        rows.extend(_fetch_history(picks, full, start, end, progress))
    if incremental:
        latest = min(bounds[c][1] for c in incremental)
        since = (date(int(latest[:4]), int(latest[4:6]), int(latest[6:8])) - timedelta(days=10)).strftime('%Y%m%d')
        rows.extend(_fetch_history(picks, incremental, max(start, since), end, progress))
    with db.transaction(db_path) as conn:
        conn.executemany('INSERT INTO local_valuation_points(trade_date,code,yield) VALUES(?,?,?) ON CONFLICT(trade_date,code) DO UPDATE SET yield=excluded.yield', [(d, c, y) for d, c, y, _ in rows])
        conn.executemany('INSERT INTO local_valuation_points(trade_date,code,yield) VALUES(?,?,?) ON CONFLICT(trade_date,code) DO UPDATE SET yield=excluded.yield', [(end, c, b['valuation_yield']) for c, b in picks.items()])


def _history_windows(start: str, end: str, days: int = 31) -> list[tuple[str, str]]:
    first = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    last = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    windows = []
    while first <= last:
        stop = min(first + timedelta(days=days - 1), last)
        windows.append((first.strftime('%Y%m%d'), stop.strftime('%Y%m%d')))
        first = stop + timedelta(days=1)
    return windows


def _fetch_history(picks: dict, codes: list[str], start: str, end: str, progress) -> list:
    """直接使用已知SECODE过滤估值表，避免大历史表与证券表的低效关联。

    估值表只有TRADEDATE索引没有SECODE索引，全年范围单查会退化为全表扫描而超时，
    按月切窗口走索引范围扫描（实测每窗口约5秒）。
    """
    mapping = {picks[c]['secode']: c for c in codes}
    chosen = {}
    marks = ','.join(f':s{i}' for i in range(len(mapping)))
    binds = {f's{i}': c for i, c in enumerate(mapping)}
    windows = _history_windows(start, end)
    with oracle_source.connect() as conn:
        conn.call_timeout = 300000
        cur = conn.cursor()
        cur.arraysize = 5000
        for ordinal, (win_start, win_end) in enumerate(windows, 1):
            progress(f'地方债历史中债估值 {win_start}~{win_end}（{len(codes)}只，{ordinal}/{len(windows)}）')
            cur.execute(f"""SELECT ID,TRADEDATE,SECODE,YIELD,VALUATIONTYPE
                FROM TQ_QT_CBESTIMATE
                WHERE TRADEDATE BETWEEN :start_date AND :end_date
                  AND DATASOURCE='1' AND ISVALID=1 AND YIELD IS NOT NULL
                  AND SECODE IN ({marks})""", {'start_date': win_start, 'end_date': win_end, **binds})
            for row_id, day, secode, value, valuation_type in cur:
                key = (str(day), mapping[str(secode)])
                rank = (0 if str(valuation_type) == '1' else 1, -int(row_id or 0))
                if key not in chosen or rank < chosen[key][0]:
                    chosen[key] = (rank, float(value))
    return [(d, c, val[1], None) for (d, c), val in chosen.items()]


def _tax_series(details: list[dict], start: str, end: str, db_path=None) -> tuple[list, list, list]:
    """省份配对税差逐日序列（活跃新税券 − 免税券，均值口径）。"""
    codes = {r[k]['code'] for r in details for k in ('active', 'exempt') if r[k]}
    matrix = {}
    if codes:
        marks = ','.join('?' for _ in codes)
        with db.connect(db_path) as conn:
            for d, c, value in conn.execute(f'SELECT trade_date,code,yield FROM local_valuation_points WHERE code IN ({marks}) AND trade_date>=? AND trade_date<=? ORDER BY trade_date', [*codes, start, end]):
                matrix.setdefault(d, {})[c] = value
    # 当前快照是末日的权威值，旧历史缓存或单只缺失都不导致末日不一致。
    matrix.setdefault(end, {}).update({r[k]['code']: r[k]['valuation_yield'] for r in details for k in ('active', 'exempt') if r[k]})
    dates, values, counts = [], [], []
    for day in sorted(matrix):
        daily = matrix[day]
        spreads = [(daily[r['active']['code']] - daily[r['exempt']['code']]) * 100 for r in details if r['active'] and r['exempt'] and r['active']['code'] in daily and r['exempt']['code'] in daily]
        dates.append(day)
        values.append(_avg(spreads))
        counts.append(len(spreads))
    return dates, values, counts


def history(group: str, range_key: str = '3m', db_path=None) -> dict:
    if group not in {g[0] for g in GROUPS}:
        raise ValueError('无效的区域分类')
    windows = {'1m': 31, '3m': 92, '6m': 183, '1y': 366}
    if range_key not in windows:
        raise ValueError('无效的时间范围')
    payload = dashboard(db_path)
    as_of = payload.get('as_of_date')
    result = {'group': group, 'range': range_key, 'as_of_date': as_of, 'dates': [], 'series': [], 'basis': 'current_selection', 'history_error': payload.get('history_error')}
    if not as_of:
        return result
    end = date(int(as_of[:4]), int(as_of[4:6]), int(as_of[6:8]))
    start = (end - timedelta(days=windows[range_key])).strftime('%Y%m%d')
    details = next(g['details'] for g in payload['comparisons'] if g['key'] == group)
    dates, values, counts = _tax_series(details, start, as_of, db_path)
    result['dates'] = dates
    result['series'].append({'key': 'tax', 'name': '活跃新税券 − 免税券', 'values': values, 'pair_counts': counts})
    return result
