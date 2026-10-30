"""30年地方债区域观察：同日估值、五交易日活跃度和同省配对。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import mean

from . import db, oracle_source

TAX_CUTOFF = "20250808"
POLICY_URL = "https://zhejiang.chinatax.gov.cn/art/2025/8/4/art_8409_84255.html"
GOOD = ("广东", "江苏", "浙江", "北京", "上海", "深圳")
BAD = ("黑龙江", "吉林", "辽宁", "云南", "贵州", "天津", "广西", "青海", "甘肃")
PROVINCES = GOOD + BAD + ("河北", "山西", "内蒙古", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "海南", "重庆", "四川", "西藏", "陕西", "宁夏", "新疆", "大连", "宁波", "厦门", "青岛")
GROUPS = (("good", "好地区", GOOD), ("middle", "中地区", tuple(p for p in PROVINCES if p not in GOOD + BAD)), ("bad", "差地区", BAD))
TABLES = (("active", "30年活跃地方债（新税券）"), ("exempt", "30年免税地方债"), ("inactive", "30年不活跃地方债（新税券）"))


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
        inactive_pool = [b for b in new if active and b["code"] != active["code"]
                         and b["avg_deals_5d"] < active["avg_deals_5d"]
                         and b["avg_deals_5d"] <= max(1, active["avg_deals_5d"] * .25)
                         and abs(b["remaining_years"] - active["remaining_years"]) <= 1
                         and b.get("duration") is not None and active.get("duration") is not None
                         and abs(b["duration"] - active["duration"]) <= 1.5]
        inactive = min(inactive_pool, key=lambda b: (b["avg_deals_5d"], -(b.get("outstanding_amount") or 0), abs(b["duration"] - active["duration"]), b["code"]), default=None)
        selected[province] = {"active": active, "exempt": exempt, "inactive": inactive}

    def spread(a, b):
        return round((a["valuation_yield"] - b["valuation_yield"]) * 100, 4) if a and b else None

    comparisons, tables = [], []
    for key, label, provinces in GROUPS:
        details = [{"province": p, **selected[p], "liquidity_bp": spread(selected[p]["active"], selected[p]["inactive"]),
                    "tax_bp": spread(selected[p]["active"], selected[p]["exempt"])} for p in provinces]
        liquidity = [r["liquidity_bp"] for r in details if r["liquidity_bp"] is not None]
        tax = [r["tax_bp"] for r in details if r["tax_bp"] is not None]
        comparisons.append({"key": key, "label": label, "liquidity_bp": _avg(liquidity), "liquidity_pairs": len(liquidity), "tax_bp": _avg(tax), "tax_pairs": len(tax), "details": details})
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
            "candidate_count": len(bonds), "unmapped_count": sum(not b.get("province") for b in bonds), "policy_url": POLICY_URL, "region_version": 2}


def refresh(db_path=None, progress=print) -> dict:
    """批量读取并原子替换快照；失败时保留上次成功数据。"""
    progress("更新30年地方债区域利差")
    with oracle_source.connect() as conn:
        conn.call_timeout = 120000
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
            for secode, first_issue in cur:
                if first_issue:
                    b = by_secode[str(secode)]
                    b['issue_date'] = min(d for d in (b['issue_date'], str(first_issue)[:8]) if d)
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


def dashboard(db_path=None) -> dict:
    raw = db.get_meta("local_dashboard", db_path=db_path)
    payload = json.loads(raw) if raw else {"as_of_date": None, "tables": [], "comparisons": []}
    if raw and payload.get('region_version') != 2:
        # 旧快照立即按新区域分组重算，保留原始选券和数据日期。
        picks = {r['bond']['code']: r['bond'] for t in payload['tables'] for g in t['groups'] for r in g['details'] if r['bond']}
        grouped = build_dashboard(list(picks.values()), payload['as_of_date'], payload['trade_days'], payload.get('benchmark'))
        grouped['candidate_count'] = payload.get('candidate_count', len(picks))
        grouped['unmapped_count'] = payload.get('unmapped_count', 0)
        payload = grouped
    payload["update_error"] = db.get_meta("local_error", db_path=db_path)
    payload['history_error'] = db.get_meta('local_history_error', db_path=db_path)
    return payload


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


def _fetch_history(picks: dict, codes: list[str], start: str, end: str, progress) -> list:
    """直接使用已知SECODE过滤估值表，避免大历史表与证券表的低效关联。"""
    progress(f'地方债历史中债估值 {start}~{end}（{len(codes)}只）')
    mapping = {picks[c]['secode']: c for c in codes}
    chosen = {}
    with oracle_source.connect() as conn:
        conn.call_timeout = 120000
        cur = conn.cursor()
        cur.arraysize = 5000
        marks = ','.join(f':s{i}' for i in range(len(mapping)))
        cur.execute(f"""SELECT ID,TRADEDATE,SECODE,YIELD,VALUATIONTYPE
            FROM TQ_QT_CBESTIMATE
            WHERE TRADEDATE BETWEEN :start_date AND :end_date
              AND DATASOURCE='1' AND ISVALID=1 AND YIELD IS NOT NULL
              AND SECODE IN ({marks})""", {'start_date': start, 'end_date': end, **{f's{i}': c for i, c in enumerate(mapping)}})
        for row_id, day, secode, value, valuation_type in cur:
            key = (str(day), mapping[str(secode)])
            rank = (0 if str(valuation_type) == '1' else 1, -int(row_id or 0))
            if key not in chosen or rank < chosen[key][0]:
                chosen[key] = (rank, float(value))
    return [(d, c, val[1], None) for (d, c), val in chosen.items()]


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
    codes = {r[k]['code'] for r in details for k in ('active', 'inactive', 'exempt') if r[k]}
    matrix = {}
    if codes:
        marks = ','.join('?' for _ in codes)
        with db.connect(db_path) as conn:
            for d, c, value in conn.execute(f'SELECT trade_date,code,yield FROM local_valuation_points WHERE code IN ({marks}) AND trade_date>=? AND trade_date<=? ORDER BY trade_date', [*codes, start, as_of]):
                matrix.setdefault(d, {})[c] = value
    # 当前快照是末日的权威值，旧历史缓存或单只缺失都不导致末日不一致。
    matrix.setdefault(as_of, {}).update({r[k]['code']: r[k]['valuation_yield'] for r in details for k in ('active', 'inactive', 'exempt') if r[k]})
    dates = sorted(matrix)
    result['dates'] = dates
    for key, right, label in (('liquidity', 'inactive', '活跃 − 不活跃'), ('tax', 'exempt', '活跃新税券 − 免税券')):
        values, counts = [], []
        for day in dates:
            daily = matrix[day]
            spreads = [(daily[r['active']['code']] - daily[r[right]['code']]) * 100 for r in details if r['active'] and r[right] and r['active']['code'] in daily and r[right]['code'] in daily]
            values.append(_avg(spreads))
            counts.append(len(spreads))
        result['series'].append({'key': key, 'name': label, 'values': values, 'pair_counts': counts})
    return result
