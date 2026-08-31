"""
骑乘效应可视化仪表盘 — 跨品种组合排序 + 历史回测
功能模块：
  1. 收益率曲线一览
  2. 骑乘收益分析
  3. 跨品种组合收益排序（替代原子弹vs哑铃模块）
  4. 历史回测对比（骑乘策略 vs 885008.WI vs 纯子弹基准）
骑乘效应与配置策略仪表盘
数据来源：聚源 Oracle 曲线 + 冻结基金指数序列
输出：纯静态 HTML (内嵌 ECharts)
"""

import json, math, datetime, os, re
import pandas as pd
import numpy as np
from pathlib import Path
from itertools import combinations

from . import config
from .db import connect, latest_curve_date
# ============================================================
# 0. 配置
# ============================================================
OUTPUT_HTML = config.STRATEGY_HTML
FROZEN_FUND_JSON = config.STRATEGY_FUND_PRICES_FROZEN
CURVES_CACHE_JSON = config.STRATEGY_CURVES_CACHE

# 回测参数
BACKTEST_START = "2023-01-03"
BACKTEST_END = datetime.date.today().strftime("%Y-%m-%d")
HOLD_PERIODS = [1, 3, 6]
TARGET_DURATIONS = [2, 3, 4, 5, 6, 7]
DEFAULT_TARGET_DUR = 4
FUND_RATE = 1.4
DATA_SOURCE_NOTE = "曲线数据: Oracle；基金指数: 冻结序列"

# 冻结基金基准来源
FUND_CODE = "885008.WI"

# ============================================================
# EDB 代码（以映射表.xlsx为准，共55个指标）
# ============================================================
EDB_CODES_STR = ",".join([
    # 政金债(国开债) 0-10Y
    "M1004258","M1004263","M1004264","M1004265","M1004266","M1004267",
    "M1004268","M1004269","M1004270","M1004688","M1004271",
    # AAA信用债(中短期票据AAA) 0-10Y
    "M1004165","S0059736","S0059737","S0059738","M0057994","S0059739",
    "M0057996","S0059740","S0167429","M1006928","S0167430",
    # AA+信用债(中短期票据AA+) 0-10Y
    "M1004167","S0059722","S0059723","S0059724","M0057993","S0059725",
    "M1006934","S0167432","M1006935","M1006936","M1004359",
    # 大行二级资本债 0-10Y
    "M1010699","M1010704","M1010705","M1010706","M1010707","M1010708",
    "M1010709","M1010710","M1010711","M1010712","M1010713",
    # 股份行二级资本债 0-10Y
    "M1010714","M1010719","M1010720","M1010721","M1010722","M1010723",
    "M1010724","M1010725","M1010726","M1010727","M1010728",
])

# EDB代码 → (品种, 期限年) 映射（严格对应映射表.xlsx）
EDB_MAPPING = {
    # --- 政金债(国开债) 0-10Y ---
    'M1004258': ('政金债', 0),
    'M1004263': ('政金债', 1),
    'M1004264': ('政金债', 2),
    'M1004265': ('政金债', 3),
    'M1004266': ('政金债', 4),
    'M1004267': ('政金债', 5),
    'M1004268': ('政金债', 6),
    'M1004269': ('政金债', 7),
    'M1004270': ('政金债', 8),
    'M1004688': ('政金债', 9),
    'M1004271': ('政金债', 10),
    # --- AAA信用债(中短期票据AAA) 0-10Y ---
    'M1004165': ('AAA信用债', 0),
    'S0059736': ('AAA信用债', 1),
    'S0059737': ('AAA信用债', 2),
    'S0059738': ('AAA信用债', 3),
    'M0057994': ('AAA信用债', 4),
    'S0059739': ('AAA信用债', 5),
    'M0057996': ('AAA信用债', 6),
    'S0059740': ('AAA信用债', 7),
    'S0167429': ('AAA信用债', 8),
    'M1006928': ('AAA信用债', 9),
    'S0167430': ('AAA信用债', 10),
    # --- AA+信用债(中短期票据AA+) 0-10Y ---
    'M1004167': ('AA+信用债', 0),
    'S0059722': ('AA+信用债', 1),
    'S0059723': ('AA+信用债', 2),
    'S0059724': ('AA+信用债', 3),
    'M0057993': ('AA+信用债', 4),
    'S0059725': ('AA+信用债', 5),
    'M1006934': ('AA+信用债', 6),
    'S0167432': ('AA+信用债', 7),
    'M1006935': ('AA+信用债', 8),
    'M1006936': ('AA+信用债', 9),
    'M1004359': ('AA+信用债', 10),
    # --- 大行二级资本债 0-10Y ---
    'M1010699': ('大行二级资本债', 0),
    'M1010704': ('大行二级资本债', 1),
    'M1010705': ('大行二级资本债', 2),
    'M1010706': ('大行二级资本债', 3),
    'M1010707': ('大行二级资本债', 4),
    'M1010708': ('大行二级资本债', 5),
    'M1010709': ('大行二级资本债', 6),
    'M1010710': ('大行二级资本债', 7),
    'M1010711': ('大行二级资本债', 8),
    'M1010712': ('大行二级资本债', 9),
    'M1010713': ('大行二级资本债', 10),
    # --- 股份行二级资本债 0-10Y ---
    'M1010714': ('股份行二级资本债', 0),
    'M1010719': ('股份行二级资本债', 1),
    'M1010720': ('股份行二级资本债', 2),
    'M1010721': ('股份行二级资本债', 3),
    'M1010722': ('股份行二级资本债', 4),
    'M1010723': ('股份行二级资本债', 5),
    'M1010724': ('股份行二级资本债', 6),
    'M1010725': ('股份行二级资本债', 7),
    'M1010726': ('股份行二级资本债', 8),
    'M1010727': ('股份行二级资本债', 9),
    'M1010728': ('股份行二级资本债', 10),
}

VARIETIES = ['政金债', 'AAA信用债', 'AA+信用债', '大行二级资本债', '股份行二级资本债']
SHORT_NAMES = {
    '政金债': '政金债', 'AAA信用债': 'AAA信用', 'AA+信用债': 'AA+信用',
    '大行二级资本债': '大行二永', '股份行二级资本债': '股份行二永',
}
COLORS = ['#2A66F6', '#F6A623', '#E5534B', '#28A745', '#8B5CF6',
          '#06B6D4', '#F43F5E', '#84CC16', '#6366F1', '#EC4899']

# ============================================================
# 1. Oracle 数据获取层 + 冻结基金序列
# ============================================================
ORACLE_CURVE_TO_VARIETY = {
    "国开债": "政金债",
    "中短票AAA": "AAA信用债",
    "中短票AA+": "AA+信用债",
    "大行二级资本债": "大行二级资本债",
    "股份行二级资本债": "股份行二级资本债",
}


def _dash_date(value):
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) >= 8 and "-" not in text else text[:10]


def _compact_date(value):
    return str(value).replace("-", "")[:8]


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _extract_js_const_array(html_text: str, const_name: str) -> list:
    marker = f"const {const_name} = "
    start = html_text.find(marker)
    if start < 0:
        raise RuntimeError(f"现有策略 HTML 中未找到 {const_name}")
    start += len(marker)
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(html_text[start:])
    if not isinstance(value, list):
        raise RuntimeError(f"{const_name} 不是数组")
    return value


def _replace_js_const(html_text: str, const_name: str, value) -> str:
    marker = f"const {const_name} = "
    start = html_text.find(marker)
    if start < 0:
        raise RuntimeError(f"现有策略 HTML 中未找到 {const_name}")
    value_start = start + len(marker)
    _, value_len = json.JSONDecoder().raw_decode(html_text[value_start:])
    semicolon = html_text.find(";", value_start + value_len)
    if semicolon < 0:
        raise RuntimeError(f"{const_name} 常量缺少结尾分号")
    replacement = marker + json.dumps(value, ensure_ascii=False) + ";"
    return html_text[:start] + replacement + html_text[semicolon + 1:]


def ensure_frozen_fund_prices() -> list[dict]:
    if FROZEN_FUND_JSON.exists():
        data = json.loads(FROZEN_FUND_JSON.read_text(encoding="utf-8"))
    else:
        if not OUTPUT_HTML.exists():
            raise RuntimeError(f"冻结基金指数不存在，且无法从旧策略页提取: {OUTPUT_HTML}")
        html = OUTPUT_HTML.read_text(encoding="utf-8")
        data = _extract_js_const_array(html, "FUND_PRICES")
        FROZEN_FUND_JSON.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(FROZEN_FUND_JSON, json.dumps(data, ensure_ascii=False, indent=2))
    rows = []
    for row in data:
        try:
            dt = str(row.get("date", ""))[:10]
            close = float(row.get("close"))
        except Exception:
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}$", dt) and close > 0:
            rows.append({"date": dt, "close": close})
    if not rows:
        raise RuntimeError(f"冻结基金指数为空或格式错误: {FROZEN_FUND_JSON}")
    rows.sort(key=lambda x: x["date"])
    return rows


def frozen_fund_dataframe(fund_prices: list[dict], end_date: str) -> pd.DataFrame:
    rows = [r for r in fund_prices if BACKTEST_START <= r["date"] <= end_date]
    if not rows:
        raise RuntimeError(f"冻结基金指数在回测区间内无数据: {BACKTEST_START} ~ {end_date}")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["close"]].sort_index()


def load_cached_oracle_curves(start_date: str, end_date: str) -> dict | None:
    payload = load_oracle_curves_cache_payload()
    if payload is None:
        return None
    data = payload.get("curves_by_date", {})
    cached_start = payload.get("start_date", "")
    cached_end = payload.get("end_date", "")
    if not data or cached_start > start_date or cached_end < end_date:
        return None
    return _normalize_cached_curves(data, start_date, end_date)


def load_oracle_curves_cache_payload() -> dict | None:
    if not CURVES_CACHE_JSON.exists():
        return None
    try:
        return json.loads(CURVES_CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_cached_curves(data: dict, start_date: str, end_date: str) -> dict | None:
    sliced = {d: data[d] for d in sorted(data) if start_date <= d <= end_date}
    if not sliced:
        return None
    return {
        d: {
            variety: {int(k) if str(k).isdigit() else float(k): float(v) for k, v in curve.items()}
            for variety, curve in day.items()
        }
        for d, day in sliced.items()
    }


def _next_calendar_date(date_str: str) -> str:
    dt = datetime.datetime.strptime(_compact_date(date_str), "%Y%m%d").date()
    return (dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def _previous_calendar_date(date_str: str) -> str:
    dt = datetime.datetime.strptime(_compact_date(date_str), "%Y%m%d").date()
    return (dt - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def save_cached_oracle_curves(curves_by_date: dict, oracle_latest: str) -> None:
    existing = {}
    if CURVES_CACHE_JSON.exists():
        try:
            existing = json.loads(CURVES_CACHE_JSON.read_text(encoding="utf-8")).get("curves_by_date", {})
        except Exception:
            existing = {}
    existing.update(curves_by_date)
    dates = sorted(existing)
    if not dates:
        return
    payload = {
        "start_date": dates[0],
        "end_date": dates[-1],
        "oracle_latest": oracle_latest,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "curves_by_date": existing,
    }
    atomic_write_text(CURVES_CACHE_JSON, json.dumps(payload, ensure_ascii=False))


def get_oracle_curves_cached(start_date: str, end_date: str, oracle_latest: str, progress=None) -> dict:
    cached = load_cached_oracle_curves(start_date, end_date)
    if cached is not None:
        if progress:
            progress(f"读取 Oracle 曲线缓存 {start_date} ~ {end_date}", 42)
        return cached
    if progress:
        progress(f"读取 Oracle 策略曲线 {start_date} ~ {end_date}", 42)
    curves = fetch_oracle_curves(start_date, end_date)
    save_cached_oracle_curves(curves, oracle_latest)
    return curves


def get_oracle_curves_cached(start_date: str, end_date: str, oracle_latest: str, progress=None) -> dict:
    cached = load_cached_oracle_curves(start_date, end_date)
    if cached is not None:
        if progress:
            progress(f"读取 Oracle 曲线缓存 {start_date} ~ {end_date}", 42)
        return cached

    payload = load_oracle_curves_cache_payload()
    data = (payload or {}).get("curves_by_date", {})
    cached_start = (payload or {}).get("start_date", "")
    cached_end = (payload or {}).get("end_date", "")
    if data and cached_start <= start_date and cached_end < end_date:
        fetch_start = _next_calendar_date(cached_end)
        if progress:
            progress(f"读取 Oracle 曲线增量 {fetch_start} ~ {end_date}", 42)
        new_curves = fetch_oracle_curves(fetch_start, end_date)
        save_cached_oracle_curves(new_curves, oracle_latest)
        normalized = _normalize_cached_curves({**data, **new_curves}, start_date, end_date)
        if normalized is not None:
            return normalized

    if data and cached_start > start_date and cached_end >= end_date:
        fetch_end = _previous_calendar_date(cached_start)
        if progress:
            progress(f"读取 Oracle 曲线补充 {start_date} ~ {fetch_end}", 42)
        new_curves = fetch_oracle_curves(start_date, fetch_end)
        save_cached_oracle_curves(new_curves, oracle_latest)
        normalized = _normalize_cached_curves({**new_curves, **data}, start_date, end_date)
        if normalized is not None:
            return normalized

    if progress:
        progress(f"读取 Oracle 策略曲线 {start_date} ~ {end_date}", 42)
    curves = fetch_oracle_curves(start_date, end_date)
    save_cached_oracle_curves(curves, oracle_latest)
    return curves


def get_latest_oracle_curve_cached(latest_date: str, progress=None) -> dict:
    cached = load_cached_oracle_curves(latest_date, latest_date)
    if cached is not None and latest_date in cached:
        if progress:
            progress(f"读取 Oracle 最新曲线缓存 {latest_date}", 42)
        return cached
    if progress:
        progress(f"读取 Oracle 最新曲线 {latest_date}", 42)
    curves = fetch_oracle_curves(latest_date, latest_date)
    save_cached_oracle_curves(curves, latest_date)
    return curves


def fetch_oracle_curves(start_date: str, end_date: str) -> dict:
    start_ymd = _compact_date(start_date)
    end_ymd = _compact_date(end_date)
    with connect() as conn:
        curves = strategy_curve_codes()
        missing = [key for key in config.STRATEGY_CURVE_DEFS if key not in curves]
        if missing:
            raise RuntimeError("未找到策略曲线: " + ", ".join(missing))
        series = {key: {} for key in curves}
        for chunk_start, chunk_end in _calendar_chunks(start_ymd, end_ymd, days=31):
            chunk_series = fetch_strategy_curve_series(conn, curves, chunk_start, chunk_end)
            for key, by_tenor in chunk_series.items():
                target = series.setdefault(key, {})
                for tenor, values in by_tenor.items():
                    target.setdefault(tenor, {}).update(values)
    base_series = series.get("国开债", {})
    dates = sorted({dt for values in base_series.values() for dt in values})
    if not dates:
        raise RuntimeError("未查询到策略曲线交易日")

    curves_by_date = {}
    for dt in dates:
        day = {v: {} for v in VARIETIES}
        for oracle_key, variety in ORACLE_CURVE_TO_VARIETY.items():
            by_tenor = series.get(oracle_key, {})
            for tenor, values in by_tenor.items():
                value = values.get(dt)
                if value is not None and value != 0:
                    day[variety][int(tenor) if float(tenor).is_integer() else float(tenor)] = float(value)
        if any(day[v] for v in VARIETIES):
            curves_by_date[_dash_date(dt)] = day
    if not curves_by_date:
        raise RuntimeError("Oracle 曲线结果为空")
    return curves_by_date


def strategy_curve_codes() -> dict[str, dict]:
    curves = {}
    for key, name in config.STRATEGY_CURVE_DEFS.items():
        code = config.CURVE_CODE_OVERRIDES.get(key)
        if code:
            curves[key] = {"code": code, "name": name, "type": "1", "keyword": name}
    return curves


def _date_chunks(dates: list[str], chunk_size: int):
    for start in range(0, len(dates), chunk_size):
        chunk = dates[start:start + chunk_size]
        if chunk:
            yield chunk[0], chunk[-1]


def _calendar_chunks(start_ymd: str, end_ymd: str, days: int = 92):
    start = datetime.datetime.strptime(start_ymd, "%Y%m%d").date()
    end = datetime.datetime.strptime(end_ymd, "%Y%m%d").date()
    current = start
    while current <= end:
        chunk_end = min(current + datetime.timedelta(days=days - 1), end)
        yield current.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        current = chunk_end + datetime.timedelta(days=1)


def fetch_strategy_dates(conn, base_curve: dict, start_date: str, end_date: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT TRADEDATE
        FROM TQ_QT_YIELDCURVE
        WHERE TRADEDATE >= :start_date
          AND TRADEDATE <= :end_date
          AND YCURVECODE = :curve_code
          AND YCURVETYPE = :curve_type
          AND ISVALID = 1
        ORDER BY TRADEDATE
        """,
        {
            "start_date": start_date,
            "end_date": end_date,
            "curve_code": base_curve["code"],
            "curve_type": base_curve["type"],
        },
    )
    return [str(row[0]) for row in cur.fetchall()]


def fetch_strategy_curve_series(conn, curve_codes: dict[str, dict], start_date: str, end_date: str) -> dict:
    code_to_key = {str(meta["code"]): key for key, meta in curve_codes.items()}
    if not code_to_key:
        return {}
    binds = {
        "start_date": start_date,
        "end_date": end_date,
        **{f"c{i}": code for i, code in enumerate(code_to_key)},
        **{f"t{i}": tenor for i, tenor in enumerate(range(11))},
    }
    code_placeholders = ",".join(f":c{i}" for i in range(len(code_to_key)))
    tenor_placeholders = ",".join(f":t{i}" for i in range(11))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT TRADEDATE, YCURVECODE, MATURITY, YIELD
        FROM TQ_QT_YIELDCURVE
        WHERE TRADEDATE >= :start_date
          AND TRADEDATE <= :end_date
          AND YCURVECODE IN ({code_placeholders})
          AND YCURVETYPE = '1'
          AND MATURITY IN ({tenor_placeholders})
          AND ISVALID = 1
        ORDER BY TRADEDATE, YCURVECODE, MATURITY
        """,
        binds,
    )
    result = {key: {} for key in curve_codes}
    for trade_date, curve_code, maturity, value in cur.fetchall():
        if maturity is None or value is None:
            continue
        key = code_to_key.get(str(curve_code))
        if not key:
            continue
        result.setdefault(key, {}).setdefault(float(maturity), {})[str(trade_date)] = float(value)
    return result

# ============================================================
# 2. 数据解析
# ============================================================
def parse_edb_to_curves(df_edb):
    """将EDB DataFrame解析为 {日期str: {品种: {期限: 收益率(%)}}}"""
    curves_by_date = {}
    for date_idx in df_edb.index:
        date_str = date_idx.strftime('%Y-%m-%d')
        curves = {v: {} for v in VARIETIES}
        for code, (variety, tenor) in EDB_MAPPING.items():
            if code in df_edb.columns:
                val = df_edb.loc[date_idx, code]
                if pd.notna(val) and val != 0:
                    curves[variety][tenor] = float(val)
        curves_by_date[date_str] = curves
    return curves_by_date

# ============================================================
# 3. 计算引擎
# ============================================================
def interpolate_yield(curve, t):
    if not curve:
        return FUND_RATE
    curve_ext = {0: FUND_RATE}
    curve_ext.update(curve)
    ks = sorted(curve_ext.keys())
    if t in curve_ext:
        return curve_ext[t]
    if t <= ks[0]:
        return curve_ext[ks[0]]
    if t >= ks[-1]:
        return curve_ext[ks[-1]]
    for i in range(len(ks) - 1):
        t1, t2 = ks[i], ks[i + 1]
        if t1 < t < t2:
            w = (t - t1) / (t2 - t1)
            return curve_ext[t1] * (1 - w) + curve_ext[t2] * w
    return curve_ext[ks[-1]]


def modified_duration(ytm_pct, maturity):
    """修正久期（par bond，年付息）: ModDur = [1-(1+y)^(-n)] / [y*(1+y)]"""
    if maturity <= 0:
        return 0.0
    y = ytm_pct / 100.0
    if abs(y) < 1e-6:
        return maturity
    try:
        mac_dur = (1 - math.pow(1 + y, -maturity)) / y
        mod_dur = mac_dur / (1 + y)
        return max(0, mod_dur)
    except:
        return maturity * 0.95


def riding_return(curve, tenor, hold_m):
    """骑乘收益(%) = [(1+ytm0/100)^tenor / (1+ytm_r/100)^rem - 1]*100"""
    if tenor <= 0 or hold_m <= 0:
        return 0.0
    hy = hold_m / 12.0
    rem = tenor - hy
    if rem <= 0:
        ytm0 = interpolate_yield(curve, tenor)
        return round(ytm0 * hy, 4)
    ytm0 = interpolate_yield(curve, tenor)
    ytm_r = interpolate_yield(curve, rem)
    r0 = ytm0 / 100.0
    rr = ytm_r / 100.0
    try:
        ret_ratio = (math.pow(1 + r0, tenor) / math.pow(1 + rr, rem)) - 1
        actual_ret = max(-10, min(10, ret_ratio * 100))
    except:
        actual_ret = 0.0
    return round(actual_ret, 4)


def static_return(ytm_pct, hold_m):
    return round(ytm_pct * (hold_m / 12.0), 4)

# ============================================================
# 4. 跨品种组合生成与排序
# ============================================================
def generate_all_portfolios(curves_dict, target_dur, hold_m=6):
    portfolios = []
    dur_tolerance = 0.15

    all_bonds = []
    for variety, curve in curves_dict.items():
        if not curve:
            continue
        for tenor, ytm in curve.items():
            if tenor <= 0 or ytm <= 0:
                continue
            md = modified_duration(ytm, tenor)
            if md > 0:
                all_bonds.append({
                    'variety': variety, 'tenor': tenor, 'ytm': ytm,
                    'dur': md, 'curve': curve,
                })

    # 单券
    for bond in all_bonds:
        if abs(bond['dur'] - target_dur) <= dur_tolerance:
            rr = riding_return(bond['curve'], bond['tenor'], hold_m)
            sr = static_return(bond['ytm'], hold_m)
            portfolios.append({
                'type': '单券',
                'variety_a': bond['variety'], 'tenor_a': bond['tenor'],
                'variety_b': '-', 'tenor_b': 0,
                'weight_a': 1.0, 'weight_b': 0.0,
                'ytm_a': bond['ytm'], 'ytm_b': 0,
                'duration': round(bond['dur'], 3),
                'riding_return': rr, 'static_return': sr,
                'excess_bp': round((rr - sr) * 100, 1),
            })

    # 两券组合
    for i in range(len(all_bonds)):
        for j in range(i + 1, len(all_bonds)):
            b1, b2 = all_bonds[i], all_bonds[j]
            d1, d2 = b1['dur'], b2['dur']
            if abs(d2 - d1) < 0.1:
                continue
            w2 = (target_dur - d1) / (d2 - d1)
            w1 = 1 - w2
            if not (0 <= w1 <= 1 and 0 <= w2 <= 1):
                continue
            rr1 = riding_return(b1['curve'], b1['tenor'], hold_m)
            rr2 = riding_return(b2['curve'], b2['tenor'], hold_m)
            combo_rr = round(w1 * rr1 + w2 * rr2, 4)
            combo_ytm = w1 * b1['ytm'] + w2 * b2['ytm']
            combo_sr = static_return(combo_ytm, hold_m)
            combo_dur = round(w1 * d1 + w2 * d2, 3)
            combo_type = '同品种哑铃' if b1['variety'] == b2['variety'] else '跨品种混搭'
            portfolios.append({
                'type': combo_type,
                'variety_a': b1['variety'], 'tenor_a': b1['tenor'],
                'variety_b': b2['variety'], 'tenor_b': b2['tenor'],
                'weight_a': round(w1, 4), 'weight_b': round(w2, 4),
                'ytm_a': b1['ytm'], 'ytm_b': b2['ytm'],
                'duration': combo_dur,
                'riding_return': combo_rr, 'static_return': combo_sr,
                'excess_bp': round((combo_rr - combo_sr) * 100, 1),
            })

    portfolios.sort(key=lambda x: x['riding_return'], reverse=True)
    return portfolios

# ============================================================
# 5. 历史回测引擎
# ============================================================
def find_future_date(dates_index, start_date, months_ahead):
    target = start_date + pd.DateOffset(months=months_ahead)
    future_dates = dates_index[dates_index >= target]
    if len(future_dates) == 0:
        return None
    return future_dates[0]


def _get_portfolios(portfolio_cache, curves_by_date, date_str, target_dur, hold_m):
    key = (date_str, target_dur, hold_m)
    if portfolio_cache is not None and key in portfolio_cache:
        return portfolio_cache[key]
    portfolios = generate_all_portfolios(curves_by_date[date_str], target_dur, hold_m)
    if portfolio_cache is not None:
        portfolio_cache[key] = portfolios
    return portfolios


def backtest_riding_strategy(curves_by_date, df_fund, target_dur, hold_m,
                             sample_freq='D', portfolio_cache=None):
    """
    回测：每个采样日确定最优组合，计算hold_m个月后的已实现收益，
    同时计算885008.WI同期收益（从收盘价计算）和纯子弹基准收益。
    """
    all_dates = sorted(curves_by_date.keys())
    dates_index = pd.DatetimeIndex(all_dates)

    if sample_freq == 'W':
        sampled = pd.Series(dates_index).groupby(dates_index.isocalendar().week).last()
        sample_dates_list = [d.strftime('%Y-%m-%d') for d in sampled.values]
    elif sample_freq == 'M':
        sampled = pd.Series(dates_index, index=dates_index).resample('ME').last().dropna()
        sample_dates_list = [d.strftime('%Y-%m-%d') for d in sampled.values]
    else:
        sample_dates_list = all_dates

    results = []
    for start_str in sample_dates_list:
        start_dt = pd.Timestamp(start_str)
        end_dt = find_future_date(dates_index, start_dt, hold_m)
        if end_dt is None:
            continue
        end_str = end_dt.strftime('%Y-%m-%d')
        if end_str not in curves_by_date:
            continue

        start_curves = curves_by_date[start_str]
        end_curves = curves_by_date[end_str]

        # 骑乘策略
        portfolios = _get_portfolios(portfolio_cache, curves_by_date, start_str, target_dur, hold_m)
        if not portfolios:
            continue
        best = portfolios[0]
        realized_ret = _calc_realized_return(best, start_curves, end_curves, hold_m)

        # 排名分位数：计算所有组合的已实现收益，排名选中策略
        all_realized = []
        for p in portfolios:
            r = _calc_realized_return(p, start_curves, end_curves, hold_m)
            all_realized.append(r)
        all_realized_sorted = sorted(all_realized, reverse=True)
        total_count = len(all_realized_sorted)
        rank_pos = all_realized_sorted.index(realized_ret) + 1  # 1-based rank
        rank_pct = round(rank_pos / total_count * 100, 2) if total_count > 0 else 0

        # 纯子弹基准
        bullet_ret = _calc_bullet_benchmark(start_curves, end_curves, target_dur, hold_m)

        # 基金收益：从收盘价计算区间收益率（允许为None）
        fund_ret = _calc_fund_return(df_fund, start_dt, end_dt)

        results.append({
            'start_date': start_str,
            'end_date': end_str,
            'riding_ret': realized_ret,
            'fund_ret': fund_ret,
            'bullet_ret': bullet_ret,
            'best_portfolio': _portfolio_label(best),
            'excess_vs_fund': round(realized_ret - fund_ret, 4) if fund_ret is not None else None,
            'excess_vs_bullet': round(realized_ret - bullet_ret, 4),
            'rank': rank_pos,
            'total': total_count,
            'rank_pct': rank_pct,
        })

    return pd.DataFrame(results)


def _calc_realized_return(portfolio, start_curves, end_curves, hold_m):
    if portfolio['type'] == '单券':
        va = portfolio['variety_a']
        ta = portfolio['tenor_a']
        return _single_realized_return(
            start_curves.get(va, {}), end_curves.get(va, {}), ta, hold_m)
    else:
        va, vb = portfolio['variety_a'], portfolio['variety_b']
        ta, tb = portfolio['tenor_a'], portfolio['tenor_b']
        wa, wb = portfolio['weight_a'], portfolio['weight_b']
        ret_a = _single_realized_return(
            start_curves.get(va, {}), end_curves.get(va, {}), ta, hold_m)
        ret_b = _single_realized_return(
            start_curves.get(vb, {}), end_curves.get(vb, {}), tb, hold_m)
        return round(wa * ret_a + wb * ret_b, 4)


def _single_realized_return(curve_start, curve_end, tenor, hold_m):
    """已实现收益(%) - Par Bond 持有期收益
    假设以面值买入（票息率=ytm0），持有hold_m个月后按curve_end的ytm_end卖出
    HPR = coupon_income + (P_sell - 1)，面值标准化为1
    """
    if not curve_start or not curve_end:
        return 0.0
    ytm0 = interpolate_yield(curve_start, tenor)
    hy = hold_m / 12.0
    rem = tenor - hy
    if rem <= 0:
        return round(ytm0 * hy, 4)
    ytm_end = interpolate_yield(curve_end, rem)

    y0 = ytm0 / 100.0   # 票息率 = 初始收益率(par bond)
    y1 = ytm_end / 100.0  # 卖出时折现率

    # 持有期票息收入
    coupon = y0 * hy

    # 卖出价格 (面值=1, 票息率=y0, 以y1折现rem年)
    try:
        if abs(y1) < 1e-8:
            p_sell = 1.0 + y0 * rem
        else:
            annuity = (1 - math.pow(1 + y1, -rem)) / y1
            p_sell = y0 * annuity + math.pow(1 + y1, -rem)
        hpr = (coupon + p_sell - 1.0) * 100
        return round(max(-10, min(10, hpr)), 4)
    except:
        return 0.0


def _calc_bullet_benchmark(start_curves, end_curves, target_dur, hold_m):
    """纯子弹基准：100%单一债券，修正久期匹配target_dur
    首选：AAA信用债（中短期票据AAA），备选：国开债（政金债）"""
    curve_start = start_curves.get('AAA信用债', {})
    curve_end = end_curves.get('AAA信用债', {})
    if not curve_start:
        curve_start = start_curves.get('政金债', {})
        curve_end = end_curves.get('政金债', {})
    if not curve_start:
        return 0.0
    best_tenor = None
    best_diff = float('inf')
    for t100 in range(50, 1201, 25):
        t = t100 / 100.0
        y = interpolate_yield(curve_start, t)
        md = modified_duration(y, t)
        diff = abs(md - target_dur)
        if diff < best_diff:
            best_diff = diff
            best_tenor = t
    if best_tenor is None:
        return 0.0
    return _single_realized_return(curve_start, curve_end, best_tenor, hold_m)


def _calc_fund_return(df_fund, start_dt, end_dt):
    """从收盘价计算区间收益率(%)：(P_end / P_start - 1) * 100"""
    try:
        start_candidates = df_fund.index[df_fund.index >= start_dt]
        end_candidates = df_fund.index[df_fund.index >= end_dt]
        if len(start_candidates) == 0 or len(end_candidates) == 0:
            return None
        price_start = df_fund.loc[start_candidates[0], 'close']
        price_end = df_fund.loc[end_candidates[0], 'close']
        if pd.isna(price_start) or pd.isna(price_end) or price_start <= 0:
            return None
        return round((price_end / price_start - 1) * 100, 4)
    except:
        return None


def _portfolio_label(p):
    sn = SHORT_NAMES
    if p['type'] == '单券':
        return f"{sn.get(p['variety_a'], p['variety_a'])}{p['tenor_a']}Y"
    else:
        va = sn.get(p['variety_a'], p['variety_a'])
        vb = sn.get(p['variety_b'], p['variety_b'])
        return f"{va}{p['tenor_a']}Y({p['weight_a']:.0%})+{vb}{p['tenor_b']}Y({p['weight_b']:.0%})"


def calc_backtest_stats(df_bt):
    if df_bt.empty:
        return {}
    stats = {}
    for col, label in [('riding_ret', '骑乘策略'), ('fund_ret', '基金指数'), ('bullet_ret', '纯子弹基准')]:
        if col not in df_bt.columns:
            continue
        rets = pd.to_numeric(df_bt[col], errors='coerce').dropna()
        if len(rets) == 0:
            continue
        stats[label] = {
            'mean_ret': round(rets.mean(), 4),
            'std': round(rets.std(), 4),
            'max': round(rets.max(), 4),
            'min': round(rets.min(), 4),
            'sharpe': round(rets.mean() / rets.std(), 4) if rets.std() > 0 else 0,
            'count': len(rets),
        }
    if 'excess_vs_fund' in df_bt.columns:
        excess = pd.to_numeric(df_bt['excess_vs_fund'], errors='coerce').dropna()
        if len(excess) > 0:
            stats['vs_fund_win_rate'] = round((excess > 0).mean() * 100, 1)
            stats['vs_fund_avg_excess'] = round(excess.mean(), 4)
    if 'excess_vs_bullet' in df_bt.columns:
        excess = pd.to_numeric(df_bt['excess_vs_bullet'], errors='coerce').dropna()
        if len(excess) > 0:
            stats['vs_bullet_win_rate'] = round((excess > 0).mean() * 100, 1)
            stats['vs_bullet_avg_excess'] = round(excess.mean(), 4)
    # 排名分位数统计
    if 'rank_pct' in df_bt.columns:
        rank_pcts = pd.to_numeric(df_bt['rank_pct'], errors='coerce').dropna()
        if len(rank_pcts) > 0:
            stats['avg_rank_pct'] = round(rank_pcts.mean(), 2)
            stats['median_rank_pct'] = round(rank_pcts.median(), 2)
            stats['top10_ratio'] = round((rank_pcts <= 10).mean() * 100, 1)
            stats['top25_ratio'] = round((rank_pcts <= 25).mean() * 100, 1)
    return stats

def _calc_cumulative_series(curves_by_date, df_fund_valid, target_dur, hold_m, portfolio_cache=None):
    """计算非重叠投资区间的复利累计收益序列 — 日频线性内插版本
    在每个持有期内，将区间收益按交易日线性分摊，消除阶梯跳升。
    返回格式: {'dates': [str], 'riding': [float], 'bullet': [float], 'fund': [float|None]}
    """
    all_dates = sorted(curves_by_date.keys())
    dates_index = pd.DatetimeIndex(all_dates)

    # --- Step 1: 计算每个非重叠区间的 riding / bullet 收益 ---
    periods = []  # [(start_str, end_str, r_ret, b_ret)]
    current_start = all_dates[0]
    while True:
        start_dt = pd.Timestamp(current_start)
        end_dt = find_future_date(dates_index, start_dt, hold_m)
        if end_dt is None:
            break
        end_str = end_dt.strftime('%Y-%m-%d')
        if end_str not in curves_by_date:
            break

        start_curves = curves_by_date[current_start]
        end_curves = curves_by_date[end_str]

        portfolios = _get_portfolios(portfolio_cache, curves_by_date, current_start, target_dur, hold_m)
        if portfolios:
            best = portfolios[0]
            r_ret = _calc_realized_return(best, start_curves, end_curves, hold_m)
        else:
            r_ret = 0.0
        b_ret = _calc_bullet_benchmark(start_curves, end_curves, target_dur, hold_m)

        periods.append((current_start, end_str, r_ret, b_ret))
        current_start = end_str

    # --- Step 2: 将 all_dates 分配到对应 period, 线性内插 ---
    # 预建 date→index 映射，避免 O(n) 查找
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    # 预计算每个 period 的 start/end index
    period_spans = []
    for p_start, p_end, pr, pb in periods:
        si = date_to_idx.get(p_start, 0)
        ei = date_to_idx.get(p_end, si)
        period_spans.append((si, ei, ei - si))

    daily_riding = []
    daily_bullet = []
    cum_r = 0.0  # 上一个已实现区间的累计(%)
    cum_b = 0.0
    period_idx = 0
    n_periods = len(periods)

    for i, d in enumerate(all_dates):
        # 确定 d 属于哪个 period（或在最后一个 period 之后）
        while period_idx < n_periods and d >= periods[period_idx][1]:
            _, _, pr, pb = periods[period_idx]
            cum_r = (1 + cum_r / 100) * (1 + pr / 100) * 100 - 100
            cum_b = (1 + cum_b / 100) * (1 + pb / 100) * 100 - 100
            period_idx += 1

        if period_idx < n_periods and d >= periods[period_idx][0]:
            # 在当前 period 内部 — 线性内插
            _, _, pr, pb = periods[period_idx]
            si, ei, total_days = period_spans[period_idx]
            elapsed = i - si
            frac = elapsed / total_days if total_days > 0 else 0.0

            interim_r = (1 + cum_r / 100) * (1 + pr * frac / 100) * 100 - 100
            interim_b = (1 + cum_b / 100) * (1 + pb * frac / 100) * 100 - 100
            daily_riding.append(round(interim_r, 4))
            daily_bullet.append(round(interim_b, 4))
        else:
            # period 之后无更多数据，保持最新累计
            daily_riding.append(round(cum_r, 4))
            daily_bullet.append(round(cum_b, 4))

    # --- Step 3: 基金日频（对齐到 all_dates）---
    daily_fund = []
    if not df_fund_valid.empty:
        base_price = df_fund_valid.iloc[0]['close']
        fund_dict = {dt.strftime('%Y-%m-%d'): row['close']
                     for dt, row in df_fund_valid.iterrows()}
        last_fund_val = None
        for d in all_dates:
            if d in fund_dict and fund_dict[d] > 0:
                last_fund_val = round((fund_dict[d] / base_price - 1) * 100, 4)
            daily_fund.append(last_fund_val)
    else:
        daily_fund = [None] * len(all_dates)

    return {'dates': all_dates, 'riding': daily_riding, 'bullet': daily_bullet, 'fund': daily_fund}


# ============================================================
# 6. 主流程
# ============================================================
def build_dashboard(progress=None) -> dict:
    def report(message, percent=None):
        if progress:
            progress(message, percent)
        else:
            print(message)

    report("读取冻结基金指数序列", 36)
    fund_prices_all = ensure_frozen_fund_prices()
    frozen_fund_end = fund_prices_all[-1]["date"]

    with connect() as conn:
        oracle_latest = _dash_date(latest_curve_date(conn))
    backtest_end = min(oracle_latest, frozen_fund_end)
    if backtest_end < BACKTEST_START:
        raise RuntimeError(f"有效回测结束日早于开始日: {backtest_end} < {BACKTEST_START}")

    curves_by_date = get_oracle_curves_cached(BACKTEST_START, oracle_latest, oracle_latest, progress=progress)
    backtest_curves_by_date = {d: curves_by_date[d] for d in sorted(curves_by_date) if BACKTEST_START <= d <= backtest_end}
    if not backtest_curves_by_date:
        raise RuntimeError(f"回测曲线为空: {BACKTEST_START} ~ {backtest_end}")
    df_fund = frozen_fund_dataframe(fund_prices_all, backtest_end)

    report("解析收益率曲线", 50)
    all_dates = sorted(curves_by_date.keys())
    latest_date = all_dates[-1]
    latest_curves = curves_by_date[latest_date]
    backtest_dates = sorted(backtest_curves_by_date.keys())

    report("计算当日组合排序", 58)
    tenors_all = sorted(set(
        t for v_curves in latest_curves.values() for t in v_curves.keys() if t > 0
    ))

    riding_data = {}
    for variety in VARIETIES:
        curve = latest_curves.get(variety, {})
        if not curve:
            continue
        riding_data[variety] = {}
        for hp in HOLD_PERIODS:
            rows = []
            for t in sorted(curve.keys()):
                if t <= 0:
                    continue
                ytm = curve[t]
                rr = riding_return(curve, t, hp)
                sr = static_return(ytm, hp)
                rows.append({'tenor': t, 'ytm': round(ytm, 4), 'riding': rr,
                             'static': sr, 'excess_bp': round((rr - sr) * 100, 1)})
            riding_data[variety][hp] = rows

    portfolio_rank_data = {}
    portfolio_cache = {}
    for td in TARGET_DURATIONS:
        portfolios_1m = _get_portfolios(portfolio_cache, curves_by_date, latest_date, td, 1)
        portfolios_3m = _get_portfolios(portfolio_cache, curves_by_date, latest_date, td, 3)
        portfolios_6m = _get_portfolios(portfolio_cache, curves_by_date, latest_date, td, 6)
        portfolio_rank_data[td] = {'1m': portfolios_1m, '3m': portfolios_3m, '6m': portfolios_6m}
    # --- 历史回测（所有目标久期）---
    report("执行历史回测", 66)
    backtest_results = {}  # {target_dur: {hold_m: df}}
    for td in TARGET_DURATIONS:
        backtest_results[td] = {}
        for hp in HOLD_PERIODS:
            df_bt = backtest_riding_strategy(
                backtest_curves_by_date, df_fund, td, hp, sample_freq='D', portfolio_cache=portfolio_cache)
            backtest_results[td][hp] = df_bt

    # --- 基金价格序列（剔除调休日0值）---
    df_fund_valid = df_fund[df_fund['close'] > 0].copy()
    fund_prices = []
    for dt in df_fund_valid.index:
        fund_prices.append({'date': dt.strftime('%Y-%m-%d'), 'close': round(df_fund_valid.loc[dt, 'close'], 4)})

    # --- 非重叠累计收益序列（每个目标久期×持有期）---
    report("计算非重叠累计收益", 74)
    cum_series_all = {}  # {td: {hp: {riding:[...], bullet:[...], fund:[...]}}}
    for td in TARGET_DURATIONS:
        cum_series_all[td] = {}
        for hp in HOLD_PERIODS:
            cum_series_all[td][hp] = _calc_cumulative_series(
                backtest_curves_by_date, df_fund_valid, td, hp, portfolio_cache=portfolio_cache)

    report("生成策略仪表盘 HTML", 82)
    html = generate_html(
        latest_date, latest_curves, tenors_all,
        riding_data, portfolio_rank_data, backtest_results, fund_prices,
        cum_series_all, backtest_curves_by_date
    )
    html = html.replace(
        f"回测: {BACKTEST_START} ~ {BACKTEST_END}",
        f"回测: {BACKTEST_START} ~ {backtest_end} | 曲线日期: {latest_date} | 基金指数: 冻结序列截至 {frozen_fund_end}",
        1,
    )
    atomic_write_text(OUTPUT_HTML, html)
    return {
        "latest_date": latest_date,
        "oracle_latest": oracle_latest,
        "backtest_end": backtest_end,
        "frozen_fund_end": frozen_fund_end,
        "curve_days": len(all_dates),
        "backtest_curve_days": len(backtest_dates),
        "fund_days": len(fund_prices),
        "output": str(OUTPUT_HTML),
    }


def build_dashboard_fast(progress=None) -> dict:
    def report(message, percent=None):
        if progress:
            progress(message, percent)
        else:
            print(message)

    if not OUTPUT_HTML.exists():
        raise RuntimeError(f"策略仪表盘 HTML 不存在，无法快速更新: {OUTPUT_HTML}")

    report("读取冻结基金指数序列", 36)
    fund_prices_all = ensure_frozen_fund_prices()
    frozen_fund_end = fund_prices_all[-1]["date"]

    with connect() as conn:
        oracle_latest = _dash_date(latest_curve_date(conn))

    latest_curves_by_date = get_latest_oracle_curve_cached(oracle_latest, progress=progress)
    latest_date = sorted(latest_curves_by_date)[-1]
    latest_curves = latest_curves_by_date[latest_date]

    report("计算最新曲线与组合排序", 58)
    tenors_all = sorted(set(
        t for v_curves in latest_curves.values() for t in v_curves.keys() if t > 0
    ))
    riding_data = {}
    for variety in VARIETIES:
        curve = latest_curves.get(variety, {})
        if not curve:
            continue
        riding_data[variety] = {}
        for hp in HOLD_PERIODS:
            rows = []
            for t in sorted(curve.keys()):
                if t <= 0:
                    continue
                ytm = curve[t]
                rr = riding_return(curve, t, hp)
                sr = static_return(ytm, hp)
                rows.append({'tenor': t, 'ytm': round(ytm, 4), 'riding': rr,
                             'static': sr, 'excess_bp': round((rr - sr) * 100, 1)})
            riding_data[variety][hp] = rows

    portfolio_cache = {}
    portfolio_rank_data = {}
    for td in TARGET_DURATIONS:
        portfolio_rank_data[td] = {
            '1m': _get_portfolios(portfolio_cache, latest_curves_by_date, latest_date, td, 1),
            '3m': _get_portfolios(portfolio_cache, latest_curves_by_date, latest_date, td, 3),
            '6m': _get_portfolios(portfolio_cache, latest_curves_by_date, latest_date, td, 6),
        }

    curve_series = []
    for idx, variety in enumerate(VARIETIES):
        curve = latest_curves.get(variety, {})
        if not curve:
            continue
        curve_series.append({
            'name': SHORT_NAMES.get(variety, variety),
            'type': 'line', 'smooth': True, 'symbol': 'circle', 'symbolSize': 6,
            'data': [round(interpolate_yield(curve, t), 4) for t in tenors_all],
            'itemStyle': {'color': COLORS[idx % len(COLORS)]},
        })
    js_curves = {
        'tenors': tenors_all,
        'series': curve_series,
        'names': [SHORT_NAMES.get(v, v) for v in VARIETIES if latest_curves.get(v)],
    }
    js_riding = {
        SHORT_NAMES.get(v, v): {str(hp): rows for hp, rows in hpd.items()}
        for v, hpd in riding_data.items()
    }

    def serialize_portfolio(p):
        return {
            'type': p['type'],
            'va': SHORT_NAMES.get(p['variety_a'], p['variety_a']),
            'ta': p['tenor_a'],
            'vb': SHORT_NAMES.get(p['variety_b'], p['variety_b']) if p['variety_b'] != '-' else '-',
            'tb': p['tenor_b'],
            'wa': p['weight_a'], 'wb': p['weight_b'],
            'dur': p['duration'], 'rr': p['riding_return'],
            'sr': p['static_return'], 'ex': p['excess_bp'],
        }
    js_portfolios = {
        str(td): {
            '1m': [serialize_portfolio(p) for p in data['1m']],
            '3m': [serialize_portfolio(p) for p in data['3m']],
            '6m': [serialize_portfolio(p) for p in data['6m']],
        }
        for td, data in portfolio_rank_data.items()
    }

    report("写入策略仪表盘 HTML", 82)
    html = OUTPUT_HTML.read_text(encoding="utf-8")
    html = _replace_js_const(html, "CURVES", js_curves)
    html = _replace_js_const(html, "RIDING", js_riding)
    html = _replace_js_const(html, "PORTFOLIOS", js_portfolios)
    html = _replace_js_const(html, "FUND_PRICES", fund_prices_all)
    meta_re = re.compile(r'数据日期: .*? \| 生成: .*? \| 回测: .*?</div>')
    meta = (
        f"数据日期: {latest_date} | 生成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"回测: {BACKTEST_START} ~ {frozen_fund_end} | 曲线日期: {latest_date} | "
        f"基金指数: 冻结序列截至 {frozen_fund_end}</div>"
    )
    html = meta_re.sub(meta, html, count=1)
    atomic_write_text(OUTPUT_HTML, html)
    return {
        "latest_date": latest_date,
        "oracle_latest": oracle_latest,
        "backtest_end": frozen_fund_end,
        "frozen_fund_end": frozen_fund_end,
        "mode": "fast_latest_curve",
        "output": str(OUTPUT_HTML),
    }


def main():
    result = build_dashboard_fast()
    print(json.dumps(result, ensure_ascii=False, indent=2))

# ============================================================
# 7. HTML 生成
# ============================================================
def generate_html(latest_date, latest_curves, tenors_all,
                  riding_data, portfolio_rank_data, backtest_results, fund_prices,
                  cum_series_all=None, curves_by_date=None):
    gen_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    # 收益率曲线
    curve_series = []
    for idx, variety in enumerate(VARIETIES):
        curve = latest_curves.get(variety, {})
        if not curve:
            continue
        curve_series.append({
            'name': SHORT_NAMES.get(variety, variety),
            'type': 'line', 'smooth': True, 'symbol': 'circle', 'symbolSize': 6,
            'data': [round(interpolate_yield(curve, t), 4) for t in tenors_all],
            'itemStyle': {'color': COLORS[idx % len(COLORS)]},
        })
    js_curves = json.dumps({
        'tenors': tenors_all, 'series': curve_series,
        'names': [SHORT_NAMES.get(v, v) for v in VARIETIES if latest_curves.get(v)],
    }, ensure_ascii=False)

    js_riding = json.dumps({
        SHORT_NAMES.get(v, v): {str(hp): rows for hp, rows in hpd.items()}
        for v, hpd in riding_data.items()
    }, ensure_ascii=False)

    def serialize_portfolio(p):
        return {
            'type': p['type'],
            'va': SHORT_NAMES.get(p['variety_a'], p['variety_a']),
            'ta': p['tenor_a'],
            'vb': SHORT_NAMES.get(p['variety_b'], p['variety_b']) if p['variety_b'] != '-' else '-',
            'tb': p['tenor_b'],
            'wa': p['weight_a'], 'wb': p['weight_b'],
            'dur': p['duration'], 'rr': p['riding_return'],
            'sr': p['static_return'], 'ex': p['excess_bp'],
        }
    js_portfolios = json.dumps({
        str(td): {'1m': [serialize_portfolio(p) for p in data['1m']],
                  '3m': [serialize_portfolio(p) for p in data['3m']],
                  '6m': [serialize_portfolio(p) for p in data['6m']]}
        for td, data in portfolio_rank_data.items()
    }, ensure_ascii=False)

    # 回测数据：按 {目标久期: {持有期: {data, stats}}} 组织
    bt_json = {}
    for td, hp_dict in backtest_results.items():
        bt_json[str(td)] = {}
        for hp, df_bt in hp_dict.items():
            if df_bt.empty:
                bt_json[str(td)][str(hp)] = {'data': [], 'stats': {}}
            else:
                bt_json[str(td)][str(hp)] = {
                    'data': df_bt.to_dict('records'),
                    'stats': calc_backtest_stats(df_bt),
                }
    js_backtest = json.dumps(bt_json, ensure_ascii=False, default=str)
    js_fund_prices = json.dumps(fund_prices, ensure_ascii=False)

    # 非重叠累计收益序列
    cum_json = {}
    if cum_series_all:
        for td, hp_dict in cum_series_all.items():
            cum_json[str(td)] = {}
            for hp, series in hp_dict.items():
                cum_json[str(td)][str(hp)] = series
    js_cum_series = json.dumps(cum_json, ensure_ascii=False)

    # 全量曲线数据（供前端筛选回测使用）
    # 格式: {date_str: {variety_short_name: {tenor_int: ytm_float}}}
    curves_for_js = {}
    if curves_by_date:
        for d_str, d_curves in curves_by_date.items():
            day_data = {}
            for variety, curve in d_curves.items():
                if curve:
                    sn = SHORT_NAMES.get(variety, variety)
                    day_data[sn] = {int(t): round(v, 4) for t, v in curve.items()}
            if day_data:
                curves_for_js[d_str] = day_data
    js_all_curves = json.dumps(curves_for_js, ensure_ascii=False)

    js_colors = json.dumps(COLORS)
    js_names = json.dumps([SHORT_NAMES.get(v, v) for v in VARIETIES if latest_curves.get(v)], ensure_ascii=False)
    js_durations = json.dumps(TARGET_DURATIONS)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>信用骑乘策略</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; background:#fff; color:#333; }}
.header {{ background:#fff; padding:24px 40px; border-bottom:1px solid #dfe6ef; box-shadow:0 2px 10px rgba(15,23,42,.06); display:flex; justify-content:space-between; align-items:center; }}
.header h1 {{ font-size:22px; font-weight:600; color:#1d4f91; }}
.header .meta {{ font-size:12px; color:#718096; }}
.container {{ max-width:1700px; margin:0 auto; padding:20px; }}
.nav-tabs {{ display:flex; gap:4px; margin-bottom:20px; background:#f1f5f9; border-radius:8px; padding:4px; }}
.nav-tabs button {{ background:transparent; border:none; padding:10px 20px; border-radius:6px; font-size:14px; font-weight:500; color:#64748b; cursor:pointer; transition:all .2s; }}
.nav-tabs button.active {{ background:#fff; color:#2563eb; box-shadow:0 1px 3px rgba(0,0,0,.1); }}
.nav-tabs button:hover:not(.active) {{ color:#334155; }}
.tab-content {{ display:none; }} .tab-content.active {{ display:block; }}
.card {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:20px; margin-bottom:20px; }}
.card-title {{ font-size:16px; font-weight:600; color:#2d3748; margin-bottom:16px; padding-left:12px; border-left:3px solid #2563eb; }}
.row {{ display:flex; gap:20px; flex-wrap:wrap; }} .col-6 {{ flex:1; min-width:500px; }}
.controls {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:16px; }}
.controls label {{ font-size:13px; color:#4a5568; white-space:nowrap; }}
.controls select {{ background:#fff; border:1px solid #cbd5e1; border-radius:6px; color:#2d3748; padding:6px 12px; font-size:13px; }}
.btn-group {{ display:inline-flex; border-radius:6px; overflow:hidden; border:1px solid #cbd5e1; }}
.btn-group button {{ background:#fff; border:none; color:#4a5568; padding:6px 16px; font-size:13px; cursor:pointer; transition:all .2s; }}
.btn-group button:not(:last-child) {{ border-right:1px solid #cbd5e1; }}
.btn-group button.active {{ background:#2563eb; color:#fff; }}
.btn-group button:hover:not(.active) {{ background:#f1f5f9; }}
.tbl-wrap {{ overflow-x:auto; max-height:600px; overflow-y:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
thead th {{ background:#f1f5f9; color:#4a5568; font-weight:500; padding:8px 10px; text-align:right; white-space:nowrap; border-bottom:2px solid #e2e8f0; position:sticky; top:0; z-index:1; }}
thead th:first-child {{ text-align:left; }}
tbody td {{ padding:7px 10px; text-align:right; border-bottom:1px solid #e2e8f0; }}
tbody td:first-child {{ text-align:left; color:#2d3748; font-weight:500; }}
tbody tr:hover {{ background:rgba(37,99,235,.06); }}
.pos {{ color:#059669; }} .neg {{ color:#dc2626; }}
.rank-badge {{ display:inline-block; width:22px; height:22px; border-radius:50%; text-align:center; line-height:22px; font-size:11px; font-weight:600; color:#fff; }}
.rank-1 {{ background:#f59e0b; }} .rank-2 {{ background:#94a3b8; }} .rank-3 {{ background:#b45309; }} .rank-n {{ background:#e2e8f0; color:#64748b; }}
.tag {{ display:inline-block; font-size:11px; padding:2px 8px; border-radius:10px; font-weight:500; }}
.tag-single {{ background:#2563eb; color:#fff; }} .tag-same {{ background:#7c3aed; color:#fff; }} .tag-cross {{ background:#059669; color:#fff; }}
.chart {{ width:100%; height:400px; }} .chart-tall {{ width:100%; height:500px; }} .chart-wide {{ width:100%; height:350px; }}
.filter-panel {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; margin:12px 0; overflow:hidden; }}
.filter-toggle {{ display:flex; align-items:center; justify-content:space-between; padding:10px 16px; cursor:pointer; user-select:none; background:#f8fafc; border-bottom:1px solid #e2e8f0; }}
.filter-toggle:hover {{ background:#f1f5f9; }}
.filter-toggle .title {{ font-size:13px; font-weight:500; color:#4a5568; }}
.filter-toggle .arrow {{ font-size:12px; color:#718096; transition:transform .2s; }}
.filter-toggle.open .arrow {{ transform:rotate(180deg); }}
.filter-body {{ display:none; padding:16px; }} .filter-body.open {{ display:block; }}
.filter-group {{ margin-bottom:12px; }}
.filter-group-label {{ font-size:12px; color:#718096; font-weight:500; margin-bottom:6px; }}
.filter-checks {{ display:flex; flex-wrap:wrap; gap:6px 12px; }}
.filter-checks label {{ font-size:12px; color:#4a5568; display:flex; align-items:center; gap:4px; cursor:pointer; }}
.filter-checks input {{ width:14px; height:14px; accent-color:#2563eb; }}
.filter-actions {{ display:flex; gap:8px; margin-top:12px; align-items:center; }}
.filter-actions button {{ padding:6px 14px; border-radius:6px; font-size:12px; font-weight:500; cursor:pointer; transition:all .2s; }}
.btn-calc {{ background:#2563eb; color:#fff; border:none; }} .btn-calc:hover {{ background:#1d4ed8; }}
.btn-link {{ background:transparent; color:#2563eb; border:1px solid #cbd5e1; }} .btn-link:hover {{ background:#f1f5f9; }}
.stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-top:16px; }}
.stat-card {{ background:linear-gradient(135deg,#fff,#f8fafc); border:1px solid #e2e8f0; border-radius:10px; padding:16px; text-align:center; }}
.stat-card .label {{ font-size:12px; color:#718096; margin-bottom:4px; }}
.stat-card .value {{ font-size:22px; font-weight:700; }}
.stat-card .sub {{ font-size:11px; color:#4a5568; margin-top:4px; }}
.footer {{ text-align:center; padding:20px; font-size:14px; color:#4a5568; border-top:1px solid #e2e8f0; margin-top:20px; }}
</style>
</head>
<body>
<div class="header">
  <h1>信用骑乘策略</h1>
  <div class="meta">数据日期: {latest_date} | 生成: {gen_time} | 回测: {BACKTEST_START} ~ {BACKTEST_END}</div>
</div>
<div class="container">
<div class="nav-tabs" id="mainNav">
  <button class="active" data-tab="tab1">收益率曲线</button>
  <button data-tab="tab2">骑乘收益分析</button>
  <button data-tab="tab3">组合收益排序</button>
  <button data-tab="tab4">历史回测</button>
</div>

<div class="tab-content active" id="tab1">
<div class="card">
  <div class="card-title">各品种收益率曲线一览</div>
  <div id="curveChart" class="chart"></div>
</div>
</div>

<div class="tab-content" id="tab2">
<div class="card">
  <div class="card-title">骑乘收益分析</div>
  <div class="controls">
    <label>持有期:</label>
    <div class="btn-group" id="holdBtns">
      <button data-v="1">1个月</button>
      <button class="active" data-v="3">3个月</button>
      <button data-v="6">6个月</button>
    </div>
  </div>
  <div class="row">
    <div class="col-6"><div id="ridingBar" class="chart"></div></div>
    <div class="col-6"><div id="enhanceHeat" class="chart"></div></div>
  </div>
  <div class="card-title" style="margin-top:20px">骑乘收益明细</div>
  <div class="tbl-wrap"><table id="ridingTable"></table></div>
</div>
</div>

<div class="tab-content" id="tab3">
<div class="card">
  <div class="card-title">最优组合方案</div>
  <div class="controls">
    <label>目标久期:</label>
    <select id="rankDurSelect"></select>
    <label style="margin-left:12px">持有期:</label>
    <div class="btn-group" id="rankHoldBtns">
      <button data-v="1m">1个月</button>
      <button data-v="3m">3个月</button>
      <button class="active" data-v="6m">6个月</button>
    </div>
    <label style="margin-left:12px">显示:</label>
    <select id="rankTopSelect">
      <option value="5">Top 5</option>
      <option value="10" selected>Top 10</option>
      <option value="20">Top 20</option>
    </select>
    <label style="margin-left:12px">图表模式:</label>
    <div class="btn-group" id="rankModeBtns">
      <button class="active" data-v="abs">绝对收益</button>
      <button data-v="diff">增强对比(bp)</button>
    </div>
  </div>
  <div class="filter-panel">
    <div class="filter-toggle" id="filterToggle">
      <span class="title">只看以下品种和期限（点击展开筛选）</span>
      <span class="arrow">▼</span>
    </div>
    <div class="filter-body" id="filterBody">
      <div class="filter-group">
        <div class="filter-group-label">品种选择</div>
        <div class="filter-checks" id="filterVarieties"></div>
      </div>
      <div class="filter-group">
        <div class="filter-group-label">期限选择</div>
        <div class="filter-checks" id="filterTenors"></div>
      </div>
      <div class="filter-actions">
        <button class="btn-calc" id="filterCalcBtn">筛选计算</button>
        <button class="btn-link" id="filterSelectAll">全选</button>
        <button class="btn-link" id="filterClearAll">清空</button>
        <button class="btn-link" id="filterReset">重置（取消筛选）</button>
        <span id="filterStatus" style="font-size:11px;color:#718096;margin-left:8px"></span>
      </div>
    </div>
  </div>
  <div id="rankChart" class="chart-tall"></div>
  <div class="card-title" style="margin-top:20px">组合排序明细表</div>
  <div class="tbl-wrap"><table id="rankTable"></table></div>
</div>
</div>

<div class="tab-content" id="tab4">
<div class="card">
  <div class="card-title">骑乘策略历史回测</div>
  <div class="controls">
    <label>目标久期:</label>
    <select id="btDurSelect"></select>
    <label style="margin-left:12px">持有期:</label>
    <div class="btn-group" id="btHoldBtns">
      <button data-v="1">1个月</button>
      <button data-v="3">3个月</button>
      <button class="active" data-v="6">6个月</button>
    </div>
  </div>
  <div class="filter-panel">
    <div class="filter-toggle" id="btFilterToggle">
      <span class="title">品种/期限筛选回测（点击展开）</span>
      <span class="arrow">▼</span>
    </div>
    <div class="filter-body" id="btFilterBody">
      <div class="filter-group">
        <div class="filter-group-label">品种选择</div>
        <div class="filter-checks" id="btFilterVarieties"></div>
      </div>
      <div class="filter-group">
        <div class="filter-group-label">期限选择</div>
        <div class="filter-checks" id="btFilterTenors"></div>
      </div>
      <div class="filter-actions">
        <button class="btn-calc" id="btFilterCalcBtn">筛选计算</button>
        <button class="btn-link" id="btFilterSelectAll">全选</button>
        <button class="btn-link" id="btFilterClearAll">清空</button>
        <button class="btn-link" id="btFilterReset">重置（使用全量数据）</button>
        <span id="btFilterStatus" style="font-size:11px;color:#718096;margin-left:8px"></span>
      </div>
    </div>
  </div>
  <div class="card-title" style="margin-top:12px">累计收益对比</div>
  <div id="btCumChart" class="chart-tall"></div>
  <div style="display:flex;align-items:center;gap:12px;margin-top:20px">
    <div class="card-title" style="margin-bottom:0">滚动超额收益（骑乘 vs <select id="excessBenchmark" style="background:#fff;border:1px solid #cbd5e1;border-radius:4px;color:#2d3748;padding:4px 8px;font-size:13px;margin-left:4px"><option value="fund">基金指数</option><option value="bullet">纯子弹基准</option></select>）</div>
  </div>
  <div id="btExcessChart" class="chart-wide"></div>
  <div class="card-title" style="margin-top:20px">策略排名分位数（在所有组合中的位置）</div>
  <div id="btRankChart" class="chart-wide"></div>
  <div class="card-title" style="margin-top:20px">关键统计指标</div>
  <div id="btStatsArea" class="stats-grid"></div>
  <div class="card-title" style="margin-top:20px">逐期回测明细</div>
  <div class="tbl-wrap"><table id="btTable"></table></div>
</div>
</div>

</div>
<div class="footer">华夏久盈固收信用小组 数据来源：Wind</div>

<script>
const CURVES = {js_curves};
const RIDING = {js_riding};
const PORTFOLIOS = {js_portfolios};
const BACKTEST = {js_backtest};
const FUND_PRICES = {js_fund_prices};
const COLORS = {js_colors};
const NAMES = {js_names};
const DURS = {js_durations};
const CUM_SERIES = {js_cum_series};
const ALL_CURVES = {js_all_curves};
const FUND_RATE = {FUND_RATE};

const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

// Tab切换
$('#mainNav').addEventListener('click', e => {{
  if (e.target.tagName !== 'BUTTON') return;
  $$('#mainNav button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  $$('.tab-content').forEach(t => t.classList.remove('active'));
  $('#' + e.target.dataset.tab).classList.add('active');
  setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
}});

// ====== Tab 1: 收益率曲线 ======
const c1 = echarts.init($('#curveChart'));
c1.setOption({{
  tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0',
    textStyle:{{color:'#2d3748',fontSize:12}},
    formatter: params => {{
      let s = '<b>' + params[0].axisValue + '</b><br>';
      params.forEach(p => {{ s += p.marker + p.seriesName + ': <b>' + p.value.toFixed(4) + '%</b><br>'; }});
      return s;
    }}
  }},
  legend: {{ data:CURVES.names, top:10, textStyle:{{color:'#4a5568',fontSize:12}} }},
  grid: {{ left:60, right:30, top:50, bottom:40 }},
  xAxis: {{ type:'category', data:CURVES.tenors.map(t=>t+'Y'),
    axisLine:{{lineStyle:{{color:'#e2e8f0'}}}}, axisLabel:{{color:'#4a5568'}} }},
  yAxis: {{ type:'value', name:'收益率(%)', nameTextStyle:{{color:'#718096'}},
    axisLine:{{lineStyle:{{color:'#e2e8f0'}}}}, axisLabel:{{color:'#4a5568',formatter:v=>v.toFixed(2)}},
    splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
  series: CURVES.series,
}});

// ====== Tab 2: 骑乘收益 ======
let curHold = '3';
const c2 = echarts.init($('#ridingBar'));
const c3 = echarts.init($('#enhanceHeat'));

function drawRiding() {{
  const validNames = NAMES.filter(nm => RIDING[nm] && RIDING[nm][curHold]);
  const tenorList = validNames.length > 0 ? RIDING[validNames[0]][curHold].map(r => r.tenor) : [];

  c2.setOption({{
    tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}} }},
    legend: {{ data:validNames, top:8, textStyle:{{color:'#4a5568',fontSize:11}} }},
    grid: {{ left:55, right:20, top:50, bottom:40 }},
    xAxis: {{ type:'category', data:tenorList.map(t=>t+'Y'),
      axisLine:{{lineStyle:{{color:'#e2e8f0'}}}}, axisLabel:{{color:'#4a5568'}} }},
    yAxis: {{ type:'value', name:'骑乘收益(%)', nameTextStyle:{{color:'#718096'}},
      axisLine:{{lineStyle:{{color:'#e2e8f0'}}}}, axisLabel:{{color:'#4a5568'}},
      splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
    series: validNames.map((nm, idx) => ({{
      name:nm, type:'bar', barGap:'5%',
      data:RIDING[nm][curHold].map(r => r.riding),
      itemStyle:{{ color:COLORS[idx], borderRadius:[2,2,0,0] }},
    }})),
  }}, true);

  // 热力图
  const heatData = [];
  validNames.forEach((nm, yi) => {{
    RIDING[nm][curHold].forEach((r, xi) => {{ heatData.push([xi, yi, r.riding]); }});
  }});
  const allVals = heatData.map(d => d[2]);
  c3.setOption({{
    tooltip: {{ backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}},
      formatter: p => '<b>'+validNames[p.value[1]]+' '+tenorList[p.value[0]]+'Y</b><br>骑乘收益: '+p.value[2].toFixed(4)+'%'
    }},
    grid: {{ left:100, right:80, top:10, bottom:40 }},
    xAxis: {{ type:'category', data:tenorList.map(t=>t+'Y'), axisLabel:{{color:'#4a5568'}} }},
    yAxis: {{ type:'category', data:validNames, axisLabel:{{color:'#4a5568',fontSize:11}} }},
    visualMap: {{ min:Math.min(...allVals), max:Math.max(...allVals), calculable:true, orient:'vertical', right:10, top:'center', textStyle:{{color:'#4a5568',fontSize:11}},
      inRange:{{ color:['#0ea5e9', '#38bdf8', '#7dd3fc', '#bae6fd', '#fca5a5', '#fecaca', '#fef2f2'] }} }},
    series: [{{ type:'heatmap', data:heatData, label:{{ show:true, color:'#2d3748', fontSize:10, formatter:p=>p.value[2].toFixed(3) }},
      itemStyle:{{ borderWidth:2, borderColor:'#fff', borderRadius:3 }} }}],
  }}, true);

  // 表格
  let h = '<thead><tr><th>品种</th><th>期限</th><th>YTM(%)</th><th>骑乘收益(%)</th><th>静态收益(%)</th><th>超额(bp)</th></tr></thead><tbody>';
  validNames.forEach(nm => {{
    RIDING[nm][curHold].forEach(r => {{
      const cls = r.excess_bp > 0 ? 'pos' : (r.excess_bp < 0 ? 'neg' : '');
      h += `<tr><td>${{nm}}</td><td>${{r.tenor}}Y</td><td>${{r.ytm.toFixed(4)}}</td><td>${{r.riding.toFixed(4)}}</td><td>${{r.static.toFixed(4)}}</td><td class="${{cls}}">${{r.excess_bp>0?'+':''}}${{r.excess_bp.toFixed(1)}}</td></tr>`;
    }});
  }});
  $('#ridingTable').innerHTML = h + '</tbody>';
}}
$('#holdBtns').addEventListener('click', e => {{
  if(e.target.tagName!=='BUTTON') return;
  $$('#holdBtns button').forEach(b=>b.classList.remove('active'));
  e.target.classList.add('active'); curHold=e.target.dataset.v; drawRiding();
}});
drawRiding();

// ====== Tab 3: 组合排序 ======
const rankDurSel = $('#rankDurSelect');
DURS.forEach(d => {{ const o=document.createElement('option'); o.value=d; o.text=d+'年'; if(d=={DEFAULT_TARGET_DUR}) o.selected=true; rankDurSel.appendChild(o); }});
let rankHold='6m', rankTop=10, rankMode='abs';
let filterActive = false;
let filterVarieties = NAMES.slice(); // 默认全选
let filterTenors = Array.from({{length:10}}, (_,i)=>i+1); // 1-10
const c4 = echarts.init($('#rankChart'));

// --- 筛选面板初始化 ---
(function initFilter() {{
  const vBox = $('#filterVarieties');
  NAMES.forEach(nm => {{
    vBox.innerHTML += `<label><input type="checkbox" value="${{nm}}" checked> ${{nm}}</label>`;
  }});
  const tBox = $('#filterTenors');
  for(let t=1;t<=10;t++) {{
    tBox.innerHTML += `<label><input type="checkbox" value="${{t}}" checked> ${{t}}Y</label>`;
  }}
}})();

$('#filterToggle').addEventListener('click', () => {{
  const toggle = $('#filterToggle');
  const body = $('#filterBody');
  toggle.classList.toggle('open');
  body.classList.toggle('open');
}});
$('#filterSelectAll').addEventListener('click', () => {{
  $$('#filterVarieties input, #filterTenors input').forEach(cb => cb.checked=true);
}});
$('#filterClearAll').addEventListener('click', () => {{
  $$('#filterVarieties input, #filterTenors input').forEach(cb => cb.checked=false);
}});
$('#filterReset').addEventListener('click', () => {{
  $$('#filterVarieties input, #filterTenors input').forEach(cb => cb.checked=true);
  filterActive = false;
  $('#filterStatus').textContent = '';
  drawRank();
}});
$('#filterCalcBtn').addEventListener('click', () => {{
  filterVarieties = Array.from($$('#filterVarieties input:checked')).map(cb=>cb.value);
  filterTenors = Array.from($$('#filterTenors input:checked')).map(cb=>+cb.value);
  filterActive = true;
  $('#filterStatus').textContent = `已筛选: ${{filterVarieties.length}}个品种, ${{filterTenors.length}}个期限`;
  drawRank();
}});

function getFilteredItems() {{
  const td=rankDurSel.value, data=PORTFOLIOS[td];
  if(!data||!data[rankHold]) return [];
  let items = data[rankHold].slice(); // 浅拷贝避免修改原数组
  if(filterActive) {{
    items = items.filter(p => {{
      if(p.type==='单券') {{
        return filterVarieties.includes(p.va) && filterTenors.includes(p.ta);
      }} else {{
        return filterVarieties.includes(p.va) && filterVarieties.includes(p.vb)
            && filterTenors.includes(p.ta) && filterTenors.includes(p.tb);
      }}
    }});
  }}
  // 重新按骑乘收益排序
  items.sort((a,b) => b.rr - a.rr);
  return items; // 返回全量筛选结果，由drawRank决定展示方式
}}

// 分层采样：取Top3 + 中位2 + 末位2，用于增强对比模式
function getSampledItems(allItems) {{
  if(allItems.length <= 7) return {{ items: allItems, labels: allItems.map((_,i)=>i<3?'推荐':'') }};
  const n = allItems.length;
  const midIdx1 = Math.floor(n*0.5);
  const midIdx2 = Math.floor(n*0.55);
  const sampled = [
    allItems[0], allItems[1], allItems[2],  // Top 3
    allItems[midIdx1], allItems[midIdx2],     // 中位 2
    allItems[n-2], allItems[n-1],             // 末位 2
  ];
  const ranks = [1, 2, 3, midIdx1+1, midIdx2+1, n-1, n];
  const labels = ['推荐#1','推荐#2','推荐#3','中位#'+ranks[3],'中位#'+ranks[4],'末位#'+ranks[5],'末位#'+ranks[6]];
  return {{ items: sampled, labels: labels, ranks: ranks }};
}}

function drawRank() {{
  const allFiltered = getFilteredItems(); // 全量筛选结果
  if(allFiltered.length===0) {{
    c4.clear();
    $('#rankTable').innerHTML='<thead><tr><th colspan="11" style="text-align:center;color:#718096">无匹配组合，请调整筛选条件</th></tr></thead>';
    return;
  }}
  // 表格和绝对值模式显示Top N
  const items = allFiltered.slice(0, rankTop);
  const cats=items.map(p => p.type==='单券' ? p.va+p.ta+'Y' : p.va+p.ta+'Y+'+p.vb+p.tb+'Y');
  const typeColors=items.map(p => p.type==='单券'?'#2563eb':(p.type==='同品种哑铃'?'#7c3aed':'#059679'));

  // 找出最优子弹型（单券）收益率作为参考线
  const td=rankDurSel.value, data=PORTFOLIOS[td];
  let refItems = data[rankHold];
  if(filterActive) refItems = allFiltered;
  const bulletItems = refItems.filter(p => p.type==='单券');
  const bestBulletRR = bulletItems.length > 0 ? Math.max(...bulletItems.map(p=>p.rr)) : null;

  if(rankMode==='diff') {{
    // ====== 增强对比模式：分层采样 + 正确bp计算 ======
    const sampled = getSampledItems(allFiltered);
    const sItems = sampled.items;
    const sLabels = sampled.labels;
    const sCats = sItems.map((p,i) => {{
      const name = p.type==='单券' ? p.va+p.ta+'Y' : p.va+p.ta+'Y+'+p.vb+p.tb+'Y';
      return sLabels[i] ? sLabels[i]+'\\n'+name : name;
    }});
    const sTypeColors = sItems.map(p => p.type==='单券'?'#2563eb':(p.type==='同品种哑铃'?'#7c3aed':'#059669'));

    // 以末位为基准计算bp差值（修正公式：% → bp 乘100）
    const baseRR = sItems[sItems.length-1].rr;
    const diffData = sItems.map(p => Math.round((p.rr - baseRR)*100*10)/10); // 正确: %差值*100=bp
    const bulletDiff = bestBulletRR !== null ? Math.round((bestBulletRR - baseRR)*100*10)/10 : null;

    c4.setOption({{
      tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}},
        formatter: params => {{
          const idx=params[0].dataIndex, p=sItems[idx];
          let s='<b>'+sLabels[idx]+'</b><br>';
          const pName = p.type==='单券' ? p.va+p.ta+'Y' : p.va+p.ta+'Y+'+p.vb+p.tb+'Y';
          s+='组合: '+pName+'<br>类型: '+p.type+'<br>';
          if(p.type!=='单券') s+='权重:'+(p.wa*100).toFixed(1)+'%/'+(p.wb*100).toFixed(1)+'%<br>';
          s+='久期:'+p.dur.toFixed(2)+'年<br>';
          s+='骑乘收益: <b>'+p.rr.toFixed(4)+'%</b><br>';
          s+='vs末位: <b>+'+diffData[idx].toFixed(1)+'bp</b>';
          return s;
        }}
      }},
      legend: {{ data:['vs末位超额(bp)','最优子弹型'], top:8 }},
      grid: {{ left:60, right:30, top:50, bottom:140 }},
      xAxis: {{ type:'category', data:sCats, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:35, interval:0 }} }},
      yAxis: {{ type:'value', name:'超额(bp)', axisLabel:{{color:'#4a5568'}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
      series: [
        {{ name:'vs末位超额(bp)', type:'bar', data:diffData, itemStyle:{{ color:p=>sTypeColors[p.dataIndex], borderRadius:[2,2,0,0] }},
          label:{{ show:true, position:'top', fontSize:11, fontWeight:'bold', color:'#2d3748', formatter:p=>'+'+p.value.toFixed(1)+'bp' }},
          markLine: bulletDiff !== null ? {{
            silent:true, symbol:'none',
            lineStyle:{{ color:'#dc2626', type:'dashed', width:2 }},
            label:{{ formatter:'最优子弹型: +'+bulletDiff.toFixed(1)+'bp', position:'insideEndTop', color:'#dc2626', fontSize:11 }},
            data:[{{ yAxis:bulletDiff }}]
          }} : undefined
        }},
      ],
    }}, true);
  }} else {{
    // ====== 绝对收益模式（优化版）======
    const allRR = items.map(p=>p.rr).concat(items.map(p=>p.sr));
    const dataMin = Math.min(...allRR);
    const dataMax = Math.max(...allRR);
    const range = dataMax - dataMin;
    // 更激进的Y轴截断：仅留5%边距，让差异更明显
    const yPad = range > 0 ? range * 0.05 : 0.01;
    const yMin = Math.floor((dataMin - yPad) * 10000) / 10000;

    c4.setOption({{
      tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}},
        formatter: params => {{
          const idx=params[0].dataIndex, p=items[idx];
          let s='<b>#'+(idx+1)+' '+cats[idx]+'</b><br>类型:'+p.type+'<br>';
          if(p.type!=='单券') s+='权重:'+(p.wa*100).toFixed(1)+'%/'+(p.wb*100).toFixed(1)+'%<br>';
          s+='久期:'+p.dur.toFixed(2)+'年<br>';
          params.forEach(pp => {{ s+=pp.marker+pp.seriesName+': '+pp.value.toFixed(4)+'%<br>'; }});
          return s;
        }}
      }},
      legend: {{ data:['骑乘收益','静态收益','最优子弹型'], top:8 }},
      grid: {{ left:60, right:30, top:50, bottom:Math.max(100,items.length>20?140:100) }},
      xAxis: {{ type:'category', data:cats, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:45, interval:0 }} }},
      yAxis: {{ type:'value', name:'收益率(%)', min:yMin, axisLabel:{{color:'#4a5568',formatter:v=>v.toFixed(4)}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
      series: [
        {{ name:'骑乘收益', type:'bar', data:items.map(p=>p.rr), itemStyle:{{ color:p=>typeColors[p.dataIndex], borderRadius:[2,2,0,0] }},
          label:{{ show:true, position:'top', fontSize:9, color:'#4a5568', formatter:p=>p.value.toFixed(4) }},
          markLine: bestBulletRR !== null ? {{
            silent:true, symbol:'none',
            lineStyle:{{ color:'#dc2626', type:'dashed', width:2 }},
            label:{{ formatter:'最优子弹型: '+bestBulletRR.toFixed(4)+'%', position:'insideEndTop', color:'#dc2626', fontSize:11 }},
            data:[{{ yAxis:bestBulletRR }}]
          }} : undefined
        }},
        {{ name:'静态收益', type:'bar', data:items.map(p=>p.sr), itemStyle:{{ color:'#cbd5e1', borderRadius:[2,2,0,0] }} }},
      ],
    }}, true);
  }}

  let h='<thead><tr><th>排名</th><th>类型</th><th>品种A</th><th>期限A</th><th>品种B</th><th>期限B</th><th>权重</th><th>久期</th><th>骑乘收益(%)</th><th>静态收益(%)</th><th>超额(bp)</th></tr></thead><tbody>';
  items.forEach((p,i) => {{
    const rc=i<3?'rank-'+(i+1):'rank-n';
    const tc=p.type==='单券'?'tag-single':(p.type==='同品种哑铃'?'tag-same':'tag-cross');
    h+=`<tr><td><span class="rank-badge ${{rc}}">${{i+1}}</span></td><td><span class="tag ${{tc}}">${{p.type}}</span></td><td>${{p.va}}</td><td>${{p.ta}}Y</td><td>${{p.vb}}</td><td>${{p.tb>0?p.tb+'Y':'-'}}</td><td>${{p.type==='单券'?'100%':(p.wa*100).toFixed(1)+'%/'+(p.wb*100).toFixed(1)+'%'}}</td><td>${{p.dur.toFixed(2)}}</td><td class="pos">${{p.rr.toFixed(4)}}</td><td>${{p.sr.toFixed(4)}}</td><td class="${{p.ex>0?'pos':'neg'}}">${{p.ex>0?'+':''}}${{p.ex.toFixed(1)}}</td></tr>`;
  }});
  $('#rankTable').innerHTML = h + '</tbody>';
}}
rankDurSel.addEventListener('change', drawRank);
$('#rankHoldBtns').addEventListener('click', e => {{
  if(e.target.tagName!=='BUTTON') return;
  $$('#rankHoldBtns button').forEach(b=>b.classList.remove('active'));
  e.target.classList.add('active'); rankHold=e.target.dataset.v; drawRank();
}});
$('#rankTopSelect').addEventListener('change', e => {{ rankTop=+e.target.value; drawRank(); }});
$('#rankModeBtns').addEventListener('click', e => {{
  if(e.target.tagName!=='BUTTON') return;
  $$('#rankModeBtns button').forEach(b=>b.classList.remove('active'));
  e.target.classList.add('active'); rankMode=e.target.dataset.v; drawRank();
}});
drawRank();

// ====== Tab 4: 历史回测 ======
const btDurSel = $('#btDurSelect');
DURS.forEach(d => {{ const o=document.createElement('option'); o.value=d; o.text=d+'年'; if(d=={DEFAULT_TARGET_DUR}) o.selected=true; btDurSel.appendChild(o); }});
let btHold='6';
const c5 = echarts.init($('#btCumChart'));
const c6 = echarts.init($('#btExcessChart'));
const c7 = echarts.init($('#btRankChart'));

function drawBacktest() {{
  const td = btDurSel.value;
  const btObj = BACKTEST[td];
  if(!btObj) return;
  const btData = btObj[btHold];
  const excessRef = $('#excessBenchmark').value || 'fund';
  if(!btData || !btData.data || btData.data.length===0) {{
    $('#btStatsArea').innerHTML='<div class="stat-card"><div class="label">暂无数据</div><div class="value">-</div></div>';
    c5.clear(); c6.clear(); c7.clear(); $('#btTable').innerHTML='';
    return;
  }}
  const records = btData.data;
  const stats = btData.stats;
  const dates = records.map(r => r.start_date);

  // === 累计收益：使用后端预计算的日频线性插值序列 ===
  const cumData = CUM_SERIES[td] && CUM_SERIES[td][btHold];
  let cumDates = [], cumRiding = [], cumBullet = [], cumFund = [];
  if (cumData) {{
    cumDates = cumData.dates || [];
    cumRiding = cumData.riding || [];
    cumBullet = cumData.bullet || [];
    cumFund = (cumData.fund || []).map(v => v === null ? undefined : v);
  }}

  c5.setOption({{
    tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}} }},
    legend: {{ data:['骑乘策略','中长期纯债基金指数','纯子弹基准'], top:8, textStyle:{{color:'#4a5568'}} }},
    grid: {{ left:60, right:30, top:50, bottom:90 }},
    xAxis: {{ type:'category', data:cumDates, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:30, formatter:v=>v.substring(0,7) }} }},
    yAxis: {{ type:'value', name:'累计收益(%)', nameTextStyle:{{color:'#718096'}}, axisLabel:{{color:'#4a5568'}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
    dataZoom: [{{ type:'slider', bottom:10, height:25, start:0, end:100, xAxisIndex:0, filterMode:'filter' }}],
    series: [
      {{ name:'骑乘策略', type:'line', data:cumRiding, smooth:true, lineStyle:{{width:2.5}}, itemStyle:{{color:'#2563eb'}}, symbol:'none' }},
      {{ name:'中长期纯债基金指数', type:'line', data:cumFund, smooth:true, lineStyle:{{width:2,type:'dashed'}}, itemStyle:{{color:'#059669'}}, symbol:'none', connectNulls:true }},
      {{ name:'纯子弹基准', type:'line', data:cumBullet, smooth:true, lineStyle:{{width:2,type:'dotted'}}, itemStyle:{{color:'#7c3aed'}}, symbol:'none' }},
    ],
  }}, true);

  // 超额收益柱状图（支持切换对比基准：基金指数 vs 纯子弹基准）
  const excessKey = excessRef === 'fund' ? 'excess_vs_fund' : 'excess_vs_bullet';
  const excessLabel = excessRef === 'fund' ? '基金' : '纯子弹';
  const excessCompareKey = excessRef === 'fund' ? 'fund_ret' : 'bullet_ret';
  c6.setOption({{
    tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}},
      formatter: params => {{
        const idx=params[0].dataIndex, r=records[idx];
        return `<b>${{r.start_date}} ~ ${{r.end_date}}</b><br>骑乘:${{(r.riding_ret||0).toFixed(4)}}%<br>${{excessLabel}}:${{(r[excessCompareKey]||0).toFixed(4)}}%<br>超额:<b>${{(r[excessKey]||0).toFixed(4)}}%</b><br>组合:${{r.best_portfolio||'-'}}`;
      }}
    }},
    grid: {{ left:60, right:30, top:20, bottom:90 }},
    xAxis: {{ type:'category', data:dates, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:30, formatter:v=>v.substring(0,7) }} }},
    yAxis: {{ type:'value', name:'超额收益(%)', axisLabel:{{color:'#4a5568'}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
    dataZoom: [{{ type:'slider', bottom:10, height:25, start:0, end:100, xAxisIndex:0, filterMode:'filter' }}],
    series: [{{ type:'bar', data:records.map(r=>r[excessKey]||0),
      itemStyle:{{ color:p=>p.value>=0?'#059669':'#dc2626', borderRadius:[2,2,0,0] }} }}],
  }}, true);

  // 排名分位数图
  const rankPcts = records.map(r => r.rank_pct != null ? r.rank_pct : null);
  const hasRank = rankPcts.some(v => v !== null);
  if(hasRank) {{
    c7.setOption({{
      tooltip: {{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}},
        formatter: params => {{
          const idx=params[0].dataIndex, r=records[idx];
          return `<b>${{r.start_date}} ~ ${{r.end_date}}</b><br>排名: 第${{r.rank||'-'}}名 / 共${{r.total||'-'}}个<br>分位数: <b>${{(r.rank_pct||0).toFixed(1)}}%</b><br>(越小越优)`;
        }}
      }},
      grid: {{ left:60, right:30, top:40, bottom:90 }},
      xAxis: {{ type:'category', data:dates, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:30, formatter:v=>v.substring(0,7) }} }},
      yAxis: {{ type:'value', name:'排名分位数(%)', min:0, max:100, inverse:true, axisLabel:{{color:'#4a5568'}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
      dataZoom: [{{ type:'slider', bottom:10, height:25, start:0, end:100, xAxisIndex:0, filterMode:'filter' }}],
      visualMap: {{ show:false, min:0, max:100, dimension:1, inRange:{{ color:['#059669','#f59e0b','#dc2626'] }} }},
      series: [{{
        type:'scatter', symbolSize:8,
        data: rankPcts.map((v,i) => v !== null ? [dates[i], v] : null).filter(v=>v),
        markLine: {{
          silent:true, symbol:'none',
          data:[
            {{ yAxis:10, lineStyle:{{color:'#059669',type:'dashed',width:1.5}}, label:{{formatter:'Top 10%',color:'#059669'}} }},
            {{ yAxis:25, lineStyle:{{color:'#f59e0b',type:'dashed',width:1.5}}, label:{{formatter:'Top 25%',color:'#f59e0b'}} }},
            {{ yAxis:50, lineStyle:{{color:'#94a3b8',type:'dotted',width:1}}, label:{{formatter:'中位数',color:'#94a3b8'}} }},
          ]
        }}
      }}],
    }}, true);
  }} else {{
    c7.clear();
  }}

  // 统计卡片
  const rS=stats['骑乘策略']||{{}}, fS=stats['基金指数']||{{}}, bS=stats['纯子弹基准']||{{}};
  let cards = '';
  cards+=`<div class="stat-card"><div class="label">骑乘策略 平均收益</div><div class="value pos">${{(rS.mean_ret||0).toFixed(4)}}%</div><div class="sub">共${{rS.count||0}}个窗口</div></div>`;
  cards+=`<div class="stat-card"><div class="label">中长期纯债基金指数 平均收益</div><div class="value">${{(fS.mean_ret||0).toFixed(4)}}%</div><div class="sub">885008.WI(收盘价计算)</div></div>`;
  cards+=`<div class="stat-card"><div class="label">纯子弹 平均收益</div><div class="value">${{(bS.mean_ret||0).toFixed(4)}}%</div><div class="sub">100%目标久期匹配</div></div>`;
  cards+=`<div class="stat-card"><div class="label">vs基金胜率</div><div class="value">${{stats.vs_fund_win_rate||0}}%</div><div class="sub">平均超额${{(stats.vs_fund_avg_excess||0).toFixed(4)}}%</div></div>`;
  cards+=`<div class="stat-card"><div class="label">vs纯子弹胜率</div><div class="value">${{stats.vs_bullet_win_rate||0}}%</div><div class="sub">平均超额${{(stats.vs_bullet_avg_excess||0).toFixed(4)}}%</div></div>`;
  cards+=`<div class="stat-card"><div class="label">骑乘夏普比率</div><div class="value">${{(rS.sharpe||0).toFixed(2)}}</div><div class="sub">收益/风险</div></div>`;
  if(stats.avg_rank_pct != null) {{
    cards+=`<div class="stat-card"><div class="label">平均排名分位</div><div class="value">${{(stats.avg_rank_pct||0).toFixed(1)}}%</div><div class="sub">中位数${{(stats.median_rank_pct||0).toFixed(1)}}%</div></div>`;
    cards+=`<div class="stat-card"><div class="label">Top10%占比</div><div class="value">${{(stats.top10_ratio||0).toFixed(1)}}%</div><div class="sub">进入前10%的比例</div></div>`;
    cards+=`<div class="stat-card"><div class="label">Top25%占比</div><div class="value">${{(stats.top25_ratio||0).toFixed(1)}}%</div><div class="sub">进入前25%的比例</div></div>`;
  }}
  $('#btStatsArea').innerHTML = cards;

  // 明细表
  let h='<thead><tr><th>起始日</th><th>结束日</th><th>骑乘(%)</th><th>基金(%)</th><th>纯子弹(%)</th><th>vs基金</th><th>vs子弹</th><th>排名</th><th>分位数(%)</th><th>最优组合</th></tr></thead><tbody>';
  records.forEach(r => {{
    const fundStr = r.fund_ret != null ? (r.fund_ret).toFixed(4) : 'N/A';
    const exFundStr = r.excess_vs_fund != null ? ((r.excess_vs_fund>0?'+':'') + r.excess_vs_fund.toFixed(4)) : 'N/A';
    const exFundCls = r.excess_vs_fund != null ? (r.excess_vs_fund>0?'pos':'neg') : '';
    const rankStr = r.rank != null ? `${{r.rank}}/${{r.total}}` : '-';
    const rankPctStr = r.rank_pct != null ? r.rank_pct.toFixed(1) : '-';
    const rankPctCls = r.rank_pct != null ? (r.rank_pct<=10?'pos':r.rank_pct<=25?'':'neg') : '';
    h+=`<tr><td>${{r.start_date}}</td><td>${{r.end_date}}</td><td>${{(r.riding_ret||0).toFixed(4)}}</td><td>${{fundStr}}</td><td>${{(r.bullet_ret||0).toFixed(4)}}</td><td class="${{exFundCls}}">${{exFundStr}}</td><td class="${{(r.excess_vs_bullet||0)>0?'pos':'neg'}}">${{(r.excess_vs_bullet||0)>0?'+':''}}${{(r.excess_vs_bullet||0).toFixed(4)}}</td><td>${{rankStr}}</td><td class="${{rankPctCls}}">${{rankPctStr}}</td><td style="font-size:11px">${{r.best_portfolio||'-'}}</td></tr>`;
  }});
  $('#btTable').innerHTML = h + '</tbody>';
}}

btDurSel.addEventListener('change', () => drawBacktest());
$('#btHoldBtns').addEventListener('click', e => {{
  if(e.target.tagName!=='BUTTON') return;
  $$('#btHoldBtns button').forEach(b=>b.classList.remove('active'));
  e.target.classList.add('active'); btHold=e.target.dataset.v; drawBacktest();
}});
$('#excessBenchmark').addEventListener('change', () => drawBacktest());
drawBacktest();

// dataZoom 相互绑定：累计收益图 <-> 滚动超额收益图
let zoomSyncing = false;
c5.on('datazoom', function(e) {{
  if (zoomSyncing) return;
  zoomSyncing = true;
  if (e.start !== undefined && e.end !== undefined) {{
    c6.dispatchAction({{ type:'dataZoom', start:e.start, end:e.end }});
  }}
  zoomSyncing = false;
}});
c6.on('datazoom', function(e) {{
  if (zoomSyncing) return;
  zoomSyncing = true;
  if (e.start !== undefined && e.end !== undefined) {{
    c5.dispatchAction({{ type:'dataZoom', start:e.start, end:e.end }});
  }}
  zoomSyncing = false;
}});

// ====== Tab 4 筛选回测引擎 ======
let btFilterActive = false;
let btFilterVarieties = NAMES.slice();
let btFilterTenors = Array.from({{length:10}}, (_,i)=>i+1);
let btFilteredResult = null; // 缓存筛选回测结果

// 初始化 Tab 4 筛选面板
(function initBtFilter() {{
  const vBox = $('#btFilterVarieties');
  NAMES.forEach(nm => {{
    vBox.innerHTML += `<label><input type="checkbox" value="${{nm}}" checked> ${{nm}}</label>`;
  }});
  const tBox = $('#btFilterTenors');
  for(let t=1;t<=10;t++) {{
    tBox.innerHTML += `<label><input type="checkbox" value="${{t}}" checked> ${{t}}Y</label>`;
  }}
}})();

$('#btFilterToggle').addEventListener('click', () => {{
  $('#btFilterToggle').classList.toggle('open');
  $('#btFilterBody').classList.toggle('open');
}});
$('#btFilterSelectAll').addEventListener('click', () => {{
  $$('#btFilterVarieties input, #btFilterTenors input').forEach(cb => cb.checked=true);
}});
$('#btFilterClearAll').addEventListener('click', () => {{
  $$('#btFilterVarieties input, #btFilterTenors input').forEach(cb => cb.checked=false);
}});
$('#btFilterReset').addEventListener('click', () => {{
  $$('#btFilterVarieties input, #btFilterTenors input').forEach(cb => cb.checked=true);
  btFilterActive = false;
  btFilteredResult = null;
  $('#btFilterStatus').textContent = '';
  drawBacktest();
}});

// --- JS 回测引擎核心函数 ---
function jsInterpolateYield(curve, t) {{
  if(!curve || Object.keys(curve).length===0) return FUND_RATE;
  const ext = Object.assign({{0: FUND_RATE}}, curve);
  const ks = Object.keys(ext).map(Number).sort((a,b)=>a-b);
  if(ext[t] !== undefined) return ext[t];
  if(t <= ks[0]) return ext[ks[0]];
  if(t >= ks[ks.length-1]) return ext[ks[ks.length-1]];
  for(let i=0; i<ks.length-1; i++) {{
    if(ks[i] < t && t < ks[i+1]) {{
      const w = (t - ks[i]) / (ks[i+1] - ks[i]);
      return ext[ks[i]] * (1-w) + ext[ks[i+1]] * w;
    }}
  }}
  return ext[ks[ks.length-1]];
}}

function jsModifiedDuration(ytmPct, maturity) {{
  if(maturity <= 0) return 0;
  const y = ytmPct / 100;
  if(Math.abs(y) < 1e-6) return maturity;
  try {{
    const macDur = (1 - Math.pow(1+y, -maturity)) / y;
    return Math.max(0, macDur / (1+y));
  }} catch(e) {{ return maturity * 0.95; }}
}}

function jsSingleRealizedReturn(curveStart, curveEnd, tenor, holdM) {{
  if(!curveStart || !curveEnd || Object.keys(curveStart).length===0 || Object.keys(curveEnd).length===0) return 0;
  const ytm0 = jsInterpolateYield(curveStart, tenor);
  const hy = holdM / 12.0;
  const rem = tenor - hy;
  if(rem <= 0) return Math.round(ytm0 * hy * 10000) / 10000;
  const ytmEnd = jsInterpolateYield(curveEnd, rem);
  const y0 = ytm0 / 100, y1 = ytmEnd / 100;
  const coupon = y0 * hy;
  try {{
    let pSell;
    if(Math.abs(y1) < 1e-8) {{ pSell = 1.0 + y0 * rem; }}
    else {{
      const annuity = (1 - Math.pow(1+y1, -rem)) / y1;
      pSell = y0 * annuity + Math.pow(1+y1, -rem);
    }}
    const hpr = (coupon + pSell - 1.0) * 100;
    return Math.round(Math.max(-10, Math.min(10, hpr)) * 10000) / 10000;
  }} catch(e) {{ return 0; }}
}}

function jsGenerateFilteredPortfolios(curvesDict, targetDur, holdM, allowedV, allowedT) {{
  const durTol = 0.15;
  const allBonds = [];
  for(const variety of allowedV) {{
    const curve = curvesDict[variety];
    if(!curve) continue;
    for(const tStr of Object.keys(curve)) {{
      const tenor = Number(tStr);
      if(tenor <= 0 || !allowedT.includes(tenor)) continue;
      const ytm = curve[tenor];
      if(!ytm || ytm <= 0) continue;
      const md = jsModifiedDuration(ytm, tenor);
      if(md > 0) allBonds.push({{ variety, tenor, ytm, dur:md, curve }});
    }}
  }}
  const portfolios = [];
  // 单券
  for(const b of allBonds) {{
    if(Math.abs(b.dur - targetDur) <= durTol) {{
      portfolios.push({{ type:'单券', va:b.variety, ta:b.tenor, vb:'-', tb:0,
        wa:1, wb:0, dur:Math.round(b.dur*1000)/1000, curve_a:b.curve }});
    }}
  }}
  // 两券组合
  for(let i=0; i<allBonds.length; i++) {{
    for(let j=i+1; j<allBonds.length; j++) {{
      const b1=allBonds[i], b2=allBonds[j];
      if(Math.abs(b2.dur - b1.dur) < 0.1) continue;
      const w2 = (targetDur - b1.dur) / (b2.dur - b1.dur);
      const w1 = 1 - w2;
      if(w1<0||w1>1||w2<0||w2>1) continue;
      const comboDur = Math.round((w1*b1.dur + w2*b2.dur)*1000)/1000;
      portfolios.push({{ type: b1.variety===b2.variety?'同品种哑铃':'跨品种混搭',
        va:b1.variety, ta:b1.tenor, vb:b2.variety, tb:b2.tenor,
        wa:Math.round(w1*10000)/10000, wb:Math.round(w2*10000)/10000,
        dur:comboDur, curve_a:b1.curve, curve_b:b2.curve }});
    }}
  }}
  return portfolios;
}}

function jsCalcBulletBenchmark(startCurves, endCurves, targetDur, holdM, allowedV, allowedT) {{
  let curveS = null, curveE = null;
  for(const v of ['AAA信用','政金债']) {{
    if(allowedV.includes(v) && startCurves[v]) {{ curveS=startCurves[v]; curveE=endCurves[v]||{{}}; break; }}
  }}
  if(!curveS) {{
    for(const v of allowedV) {{
      if(startCurves[v]) {{ curveS=startCurves[v]; curveE=endCurves[v]||{{}}; break; }}
    }}
  }}
  if(!curveS) return 0;
  let bestT=null, bestDiff=Infinity;
  for(let t100=50; t100<=1200; t100+=25) {{
    const t=t100/100;
    if(!allowedT.includes(Math.round(t)) && t%1!==0) continue;
    const y = jsInterpolateYield(curveS, t);
    const md = jsModifiedDuration(y, t);
    const diff = Math.abs(md - targetDur);
    if(diff < bestDiff) {{ bestDiff=diff; bestT=t; }}
  }}
  if(bestT===null) return 0;
  return jsSingleRealizedReturn(curveS, curveE, bestT, holdM);
}}

function jsCalcFundReturn(startStr, endStr) {{
  if(!FUND_PRICES || FUND_PRICES.length===0) return null;
  let pStart=null, pEnd=null;
  for(const fp of FUND_PRICES) {{
    if(!pStart && fp.date >= startStr) pStart = fp.close;
    if(!pEnd && fp.date >= endStr) pEnd = fp.close;
    if(pStart && pEnd) break;
  }}
  if(!pStart || !pEnd || pStart<=0) return null;
  return Math.round((pEnd/pStart - 1)*100*10000)/10000;
}}

function jsPortfolioLabel(p) {{
  if(p.type==='单券') return p.va+p.ta+'Y';
  return p.va+p.ta+'Y('+(p.wa*100).toFixed(0)+'%)+'+p.vb+p.tb+'Y('+(p.wb*100).toFixed(0)+'%)';
}}

function jsRunFilteredBacktest(targetDur, holdM, allowedV, allowedT) {{
  const allDates = Object.keys(ALL_CURVES).sort();
  if(allDates.length === 0) return {{ records:[], cumSeries:null }};

  // 周频采样
  const sampleDates = [];
  let lastWeek = -1;
  for(const d of allDates) {{
    const dt = new Date(d);
    const week = Math.floor(dt.getTime() / (7*86400000));
    if(week !== lastWeek) {{ sampleDates.push(d); lastWeek = week; }}
  }}

  // 查找未来日期
  function findFuture(startStr, months) {{
    const sd = new Date(startStr);
    sd.setMonth(sd.getMonth() + months);
    const target = sd.toISOString().slice(0,10);
    for(const d of allDates) {{ if(d >= target) return d; }}
    return null;
  }}

  const records = [];
  for(const startStr of sampleDates) {{
    const endStr = findFuture(startStr, holdM);
    if(!endStr || !ALL_CURVES[endStr]) continue;
    const startC = ALL_CURVES[startStr];
    const endC = ALL_CURVES[endStr];

    const portfolios = jsGenerateFilteredPortfolios(startC, targetDur, holdM, allowedV, allowedT);
    if(portfolios.length === 0) continue;

    // 按骑乘预期排序，取最优
    portfolios.sort((a,b) => {{
      const rrA = a.type==='单券' ?
        jsSingleRealizedReturn(a.curve_a, endC[a.va]||{{}}, a.ta, holdM) :
        a.wa*jsSingleRealizedReturn(a.curve_a, endC[a.va]||{{}}, a.ta, holdM) +
        a.wb*jsSingleRealizedReturn(a.curve_b, endC[a.vb]||{{}}, a.tb, holdM);
      const rrB = b.type==='单券' ?
        jsSingleRealizedReturn(b.curve_a, endC[b.va]||{{}}, b.ta, holdM) :
        b.wa*jsSingleRealizedReturn(b.curve_a, endC[b.va]||{{}}, b.ta, holdM) +
        b.wb*jsSingleRealizedReturn(b.curve_b, endC[b.vb]||{{}}, b.tb, holdM);
      return rrB - rrA;
    }});

    const best = portfolios[0];
    let ridingRet;
    if(best.type==='单券') {{
      ridingRet = jsSingleRealizedReturn(startC[best.va]||{{}}, endC[best.va]||{{}}, best.ta, holdM);
    }} else {{
      ridingRet = best.wa * jsSingleRealizedReturn(startC[best.va]||{{}}, endC[best.va]||{{}}, best.ta, holdM) +
                  best.wb * jsSingleRealizedReturn(startC[best.vb]||{{}}, endC[best.vb]||{{}}, best.tb, holdM);
      ridingRet = Math.round(ridingRet*10000)/10000;
    }}

    const bulletRet = jsCalcBulletBenchmark(startC, endC, targetDur, holdM, allowedV, allowedT);
    const fundRet = jsCalcFundReturn(startStr, endStr);

    records.push({{
      start_date: startStr, end_date: endStr,
      riding_ret: ridingRet, fund_ret: fundRet, bullet_ret: bulletRet,
      best_portfolio: jsPortfolioLabel(best),
      excess_vs_fund: fundRet !== null ? Math.round((ridingRet-fundRet)*10000)/10000 : null,
      excess_vs_bullet: Math.round((ridingRet-bulletRet)*10000)/10000,
    }});
  }}

  // 累计收益曲线（非重叠区间 + 日频内插）
  const periods = [];
  let curStart = allDates[0];
  while(true) {{
    const eStr = findFuture(curStart, holdM);
    if(!eStr || !ALL_CURVES[eStr]) break;
    const sC = ALL_CURVES[curStart], eC = ALL_CURVES[eStr];
    const ps = jsGenerateFilteredPortfolios(sC, targetDur, holdM, allowedV, allowedT);
    let rRet = 0;
    if(ps.length > 0) {{
      ps.sort((a,b) => {{
        const ra = a.type==='单券' ? jsSingleRealizedReturn(a.curve_a, eC[a.va]||{{}}, a.ta, holdM) :
          a.wa*jsSingleRealizedReturn(a.curve_a, eC[a.va]||{{}}, a.ta, holdM)+a.wb*jsSingleRealizedReturn(a.curve_b, eC[a.vb]||{{}}, a.tb, holdM);
        const rb = b.type==='单券' ? jsSingleRealizedReturn(b.curve_a, eC[b.va]||{{}}, b.ta, holdM) :
          b.wa*jsSingleRealizedReturn(b.curve_a, eC[b.va]||{{}}, b.ta, holdM)+b.wb*jsSingleRealizedReturn(b.curve_b, eC[b.vb]||{{}}, b.tb, holdM);
        return rb - ra;
      }});
      const bst = ps[0];
      rRet = bst.type==='单券' ? jsSingleRealizedReturn(sC[bst.va]||{{}}, eC[bst.va]||{{}}, bst.ta, holdM) :
        bst.wa*jsSingleRealizedReturn(sC[bst.va]||{{}}, eC[bst.va]||{{}}, bst.ta, holdM)+bst.wb*jsSingleRealizedReturn(sC[bst.vb]||{{}}, eC[bst.vb]||{{}}, bst.tb, holdM);
    }}
    const bRet = jsCalcBulletBenchmark(sC, eC, targetDur, holdM, allowedV, allowedT);
    periods.push([curStart, eStr, rRet, bRet]);
    curStart = eStr;
  }}

  // 日频线性内插
  const dateIdx = {{}};
  allDates.forEach((d,i) => dateIdx[d]=i);
  const cumRiding=[], cumBullet=[], cumFund=[];
  let cumR=0, cumB=0, pIdx=0;
  const baseFP = FUND_PRICES.length>0 ? FUND_PRICES[0].close : null;
  const fpMap = {{}};
  FUND_PRICES.forEach(fp => fpMap[fp.date]=fp.close);
  let lastFV = null;

  for(let i=0; i<allDates.length; i++) {{
    const d = allDates[i];
    while(pIdx < periods.length && d >= periods[pIdx][1]) {{
      cumR = (1+cumR/100)*(1+periods[pIdx][2]/100)*100 - 100;
      cumB = (1+cumB/100)*(1+periods[pIdx][3]/100)*100 - 100;
      pIdx++;
    }}
    if(pIdx < periods.length && d >= periods[pIdx][0]) {{
      const si = dateIdx[periods[pIdx][0]]||0;
      const ei = dateIdx[periods[pIdx][1]]||si;
      const total = ei - si;
      const frac = total > 0 ? (i-si)/total : 0;
      cumRiding.push(Math.round(((1+cumR/100)*(1+periods[pIdx][2]*frac/100)*100-100)*10000)/10000);
      cumBullet.push(Math.round(((1+cumB/100)*(1+periods[pIdx][3]*frac/100)*100-100)*10000)/10000);
    }} else {{
      cumRiding.push(Math.round(cumR*10000)/10000);
      cumBullet.push(Math.round(cumB*10000)/10000);
    }}
    if(baseFP && fpMap[d] && fpMap[d]>0) lastFV = Math.round((fpMap[d]/baseFP-1)*100*10000)/10000;
    cumFund.push(lastFV);
  }}

  return {{ records, cumSeries: {{ dates:allDates, riding:cumRiding, bullet:cumBullet, fund:cumFund }} }};
}}

function jsCalcStats(records) {{
  if(records.length===0) return {{}};
  const ridingRets = records.map(r=>r.riding_ret).filter(v=>v!=null);
  const mean = ridingRets.reduce((a,b)=>a+b,0)/ridingRets.length;
  const std = Math.sqrt(ridingRets.map(v=>(v-mean)**2).reduce((a,b)=>a+b,0)/ridingRets.length);
  const stats = {{
    '骑乘策略': {{ mean_ret:Math.round(mean*10000)/10000, std:Math.round(std*10000)/10000,
      max:Math.round(Math.max(...ridingRets)*10000)/10000, min:Math.round(Math.min(...ridingRets)*10000)/10000,
      sharpe: std>0 ? Math.round(mean/std*10000)/10000 : 0, count:ridingRets.length }},
  }};
  const fundRets = records.map(r=>r.fund_ret).filter(v=>v!=null);
  if(fundRets.length>0) {{
    const fm = fundRets.reduce((a,b)=>a+b,0)/fundRets.length;
    stats['基金指数'] = {{ mean_ret:Math.round(fm*10000)/10000, count:fundRets.length }};
  }}
  const bulletRets = records.map(r=>r.bullet_ret).filter(v=>v!=null);
  if(bulletRets.length>0) {{
    const bm = bulletRets.reduce((a,b)=>a+b,0)/bulletRets.length;
    stats['纯子弹基准'] = {{ mean_ret:Math.round(bm*10000)/10000, count:bulletRets.length }};
  }}
  const exFund = records.map(r=>r.excess_vs_fund).filter(v=>v!=null);
  if(exFund.length>0) {{
    stats.vs_fund_win_rate = Math.round(exFund.filter(v=>v>0).length/exFund.length*1000)/10;
    stats.vs_fund_avg_excess = Math.round(exFund.reduce((a,b)=>a+b,0)/exFund.length*10000)/10000;
  }}
  const exBullet = records.map(r=>r.excess_vs_bullet).filter(v=>v!=null);
  if(exBullet.length>0) {{
    stats.vs_bullet_win_rate = Math.round(exBullet.filter(v=>v>0).length/exBullet.length*1000)/10;
    stats.vs_bullet_avg_excess = Math.round(exBullet.reduce((a,b)=>a+b,0)/exBullet.length*10000)/10000;
  }}
  return stats;
}}

// 筛选计算按钮
$('#btFilterCalcBtn').addEventListener('click', () => {{
  btFilterVarieties = Array.from($$('#btFilterVarieties input:checked')).map(cb=>cb.value);
  btFilterTenors = Array.from($$('#btFilterTenors input:checked')).map(cb=>+cb.value);
  if(btFilterVarieties.length===0 || btFilterTenors.length===0) {{
    $('#btFilterStatus').textContent = '请至少选择1个品种和1个期限';
    return;
  }}
  btFilterActive = true;
  $('#btFilterStatus').textContent = '计算中...';
  setTimeout(() => {{
    const td = +btDurSel.value;
    const hm = +btHold;
    btFilteredResult = jsRunFilteredBacktest(td, hm, btFilterVarieties, btFilterTenors);
    $('#btFilterStatus').textContent = `已筛选: ${{btFilterVarieties.length}}个品种, ${{btFilterTenors.length}}个期限 (共${{btFilteredResult.records.length}}个窗口)`;
    drawBacktest();
  }}, 50);
}});

// 修改 drawBacktest 以支持筛选模式
const _origDrawBacktest = drawBacktest;
drawBacktest = function() {{
  if(btFilterActive && btFilteredResult) {{
    const td = btDurSel.value;
    const excessRef = $('#excessBenchmark').value || 'fund';
    const records = btFilteredResult.records;
    const cumSeries = btFilteredResult.cumSeries;
    if(records.length === 0) {{
      $('#btStatsArea').innerHTML='<div class="stat-card"><div class="label">无匹配结果</div><div class="value">-</div><div class="sub">请调整品种/期限筛选</div></div>';
      c5.clear(); c6.clear(); c7.clear(); $('#btTable').innerHTML='';
      return;
    }}
    const stats = jsCalcStats(records);
    const dates = records.map(r=>r.start_date);

    // 累计收益图
    const cumDates = cumSeries ? cumSeries.dates : [];
    const cumRiding = cumSeries ? cumSeries.riding : [];
    const cumBullet = cumSeries ? cumSeries.bullet : [];
    const cumFund = cumSeries ? cumSeries.fund.map(v=>v===null?undefined:v) : [];

    c5.setOption({{
      tooltip:{{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}} }},
      legend:{{ data:['骑乘策略(筛选)','中长期纯债基金指数','纯子弹基准(筛选)'], top:8, textStyle:{{color:'#4a5568'}} }},
      grid:{{ left:60, right:30, top:50, bottom:90 }},
      xAxis:{{ type:'category', data:cumDates, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:30, formatter:v=>v.substring(0,7) }} }},
      yAxis:{{ type:'value', name:'累计收益(%)', nameTextStyle:{{color:'#718096'}}, axisLabel:{{color:'#4a5568'}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
      dataZoom:[{{ type:'slider', bottom:10, height:25, start:0, end:100 }}],
      series:[
        {{ name:'骑乘策略(筛选)', type:'line', data:cumRiding, smooth:true, lineStyle:{{width:2.5}}, itemStyle:{{color:'#2563eb'}}, symbol:'none' }},
        {{ name:'中长期纯债基金指数', type:'line', data:cumFund, smooth:true, lineStyle:{{width:2,type:'dashed'}}, itemStyle:{{color:'#059669'}}, symbol:'none', connectNulls:true }},
        {{ name:'纯子弹基准(筛选)', type:'line', data:cumBullet, smooth:true, lineStyle:{{width:2,type:'dotted'}}, itemStyle:{{color:'#7c3aed'}}, symbol:'none' }},
      ],
    }}, true);

    // 超额收益
    const excessKey = excessRef==='fund' ? 'excess_vs_fund' : 'excess_vs_bullet';
    c6.setOption({{
      tooltip:{{ trigger:'axis', backgroundColor:'#fff', borderColor:'#e2e8f0', textStyle:{{color:'#2d3748',fontSize:12}} }},
      grid:{{ left:60, right:30, top:20, bottom:90 }},
      xAxis:{{ type:'category', data:dates, axisLabel:{{ color:'#4a5568', fontSize:10, rotate:30, formatter:v=>v.substring(0,7) }} }},
      yAxis:{{ type:'value', name:'超额收益(%)', axisLabel:{{color:'#4a5568'}}, splitLine:{{lineStyle:{{color:'#f1f5f9'}}}} }},
      dataZoom:[{{ type:'slider', bottom:10, height:25, start:0, end:100 }}],
      series:[{{ type:'bar', data:records.map(r=>r[excessKey]||0),
        itemStyle:{{ color:p=>p.value>=0?'#059669':'#dc2626', borderRadius:[2,2,0,0] }} }}],
    }}, true);

    // 统计卡片
    const rS=stats['骑乘策略']||{{}}, fS=stats['基金指数']||{{}}, bS=stats['纯子弹基准']||{{}};
    let cards='';
    cards+=`<div class="stat-card"><div class="label">骑乘策略(筛选) 平均收益</div><div class="value pos">${{(rS.mean_ret||0).toFixed(4)}}%</div><div class="sub">共${{rS.count||0}}个窗口</div></div>`;
    cards+=`<div class="stat-card"><div class="label">基金指数 平均收益</div><div class="value">${{(fS.mean_ret||0).toFixed(4)}}%</div><div class="sub">885008.WI</div></div>`;
    cards+=`<div class="stat-card"><div class="label">纯子弹(筛选) 平均收益</div><div class="value">${{(bS.mean_ret||0).toFixed(4)}}%</div><div class="sub">受限品种/期限</div></div>`;
    cards+=`<div class="stat-card"><div class="label">vs基金胜率</div><div class="value">${{stats.vs_fund_win_rate||0}}%</div><div class="sub">平均超额${{(stats.vs_fund_avg_excess||0).toFixed(4)}}%</div></div>`;
    cards+=`<div class="stat-card"><div class="label">vs纯子弹胜率</div><div class="value">${{stats.vs_bullet_win_rate||0}}%</div><div class="sub">平均超额${{(stats.vs_bullet_avg_excess||0).toFixed(4)}}%</div></div>`;
    cards+=`<div class="stat-card"><div class="label">夏普比率</div><div class="value">${{(rS.sharpe||0).toFixed(2)}}</div><div class="sub">收益/风险</div></div>`;
    $('#btStatsArea').innerHTML = cards;

    // 明细表
    let h='<thead><tr><th>起始日</th><th>结束日</th><th>骑乘(%)</th><th>基金(%)</th><th>纯子弹(%)</th><th>vs基金</th><th>vs子弹</th><th>最优组合</th></tr></thead><tbody>';
    records.forEach(r => {{
      const fundStr = r.fund_ret!=null ? r.fund_ret.toFixed(4) : 'N/A';
      const exFundStr = r.excess_vs_fund!=null ? ((r.excess_vs_fund>0?'+':'')+r.excess_vs_fund.toFixed(4)) : 'N/A';
      const exFundCls = r.excess_vs_fund!=null ? (r.excess_vs_fund>0?'pos':'neg') : '';
      h+=`<tr><td>${{r.start_date}}</td><td>${{r.end_date}}</td><td>${{(r.riding_ret||0).toFixed(4)}}</td><td>${{fundStr}}</td><td>${{(r.bullet_ret||0).toFixed(4)}}</td><td class="${{exFundCls}}">${{exFundStr}}</td><td class="${{(r.excess_vs_bullet||0)>0?'pos':'neg'}}">${{(r.excess_vs_bullet||0)>0?'+':''}}${{(r.excess_vs_bullet||0).toFixed(4)}}</td><td style="font-size:11px">${{r.best_portfolio||'-'}}</td></tr>`;
    }});
    $('#btTable').innerHTML = h + '</tbody>';
    return;
  }}
  _origDrawBacktest();
}};

// 当切换目标久期/持有期时，如果筛选模式激活，重新计算
const _origBtDurChange = btDurSel.onchange;
btDurSel.addEventListener('change', () => {{
  if(btFilterActive) {{
    btFilteredResult = null;
    $('#btFilterStatus').textContent = '参数已变更，请重新点击"筛选计算"';
    btFilterActive = false;
  }}
}});
$('#btHoldBtns').addEventListener('click', e => {{
  if(btFilterActive && e.target.tagName==='BUTTON') {{
    btFilteredResult = null;
    $('#btFilterStatus').textContent = '参数已变更，请重新点击"筛选计算"';
    btFilterActive = false;
  }}
}});

window.addEventListener('resize', () => {{ [c1,c2,c3,c4,c5,c6,c7].forEach(c => {{ try{{c.resize();}}catch(e){{}} }}); }});
</script>
</body>
</html>'''
    return html


if __name__ == '__main__':
    main()
