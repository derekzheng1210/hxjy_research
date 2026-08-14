"""
一级发行非市场化评估系统 - 计算引擎

核心逻辑：
1. 对每只新发债券，找到发行人最接近期限的存续债
2. 计算存续债估值与评级曲线的偏离（spread）
3. 合理价格 = 目标期限曲线值 + spread
4. 一级偏离 = 票面利率 - 合理价格
5. 按发行人汇总非市场化发行比例

性能优化：
- 同一发行人的存续债列表和估值只查一次
- 曲线数据按(curve_code, date)缓存
- 用逐日回退策略替代慢MAX查询
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from . import config
from .data_fetcher import (
    fetch_all_valuations_for_date,
    fetch_bond_valuations,
    fetch_curve,
    fetch_implied_ratings,
    fetch_issuer_outstanding,
    fetch_new_issues,
)


# ──────────────────────────────────────────────────────
# 债券品种分类（用于同品种对标过滤）
# ──────────────────────────────────────────────────────

NO_JUDGEMENT_DATE_GAP_YEARS = 2.0


def _date_gap_years(date_a: str, date_b: str) -> Optional[float]:
    """Return absolute date gap in years for YYYYMMDD strings."""
    if not date_a or not date_b:
        return None
    try:
        d1 = datetime.strptime(str(date_a), "%Y%m%d")
        d2 = datetime.strptime(str(date_b), "%Y%m%d")
    except ValueError:
        return None
    return abs((d1 - d2).days) / 365.0

# 永续债名称尾部模式：Y1, Y2, Y01, Y02, ...（字母Y+数字结尾）
_PERPETUAL_SUFFIX_RE = re.compile(r"Y\d+$", re.IGNORECASE)
_BROKER_SUBORDINATED_SUFFIX_RE = re.compile(r"C\d+$", re.IGNORECASE)


def _is_perpetual_by_field(cvtbd_expire: Optional[str]) -> bool:
    """
    根据含权期限说明字段（TQ_BD_BASICINFO.CVTBDEXPIREMEMP）判断是否永续债。

    永续债该字段形如 "3+N"、"5+N"、"1+1+N"（N 表示无固定到期日、发行人续期选择权），
    普通含权债形如 "2+1"、"3+2"。

    该字段可识别名称无永续标记的永续债（如 26潞安MTN003B），
    经验证覆盖率约 99.9%（名称可识别的永续债中仅个别该字段为空）。
    """
    if cvtbd_expire is None:
        return False
    memo = str(cvtbd_expire).strip().upper()
    if not memo or memo == "NONE" or memo == "NAN":
        return False
    return memo.endswith("+N")


def classify_bond_type(bond_name: str, cvtbd_expire: Optional[str] = None) -> str:
    """
    判断债券品种类别。

    识别优先级：
    1. 名称含"二级" → 二级资本债
    2. 名称含"TLAC" → TLAC
    3. 含权期限说明字段（CVTBDEXPIREMEMP）以"+N"结尾 → 永续债（主识别方式）
    4. 名称含"永续"或尾部为Y+数字 → 永续债（名称兜底，覆盖字段为空的个例）

    Args:
        bond_name: 债券简称（BONDSNAME）
        cvtbd_expire: 含权期限说明（CVTBDEXPIREMEMP），如 "3+N"、"2+1"，可为空

    Returns:
        "perpetual" - 永续债
        "tier2"     - 二级资本债
        "tlac"      - TLAC（总损失吸收能力）
        "ordinary"  - 普通债
    """
    name = (bond_name or "").strip()

    # 二级资本债（名称含"二级"）
    if "二级" in name:
        return "tier2"

    # TLAC（名称含TLAC，不区分大小写）
    if "TLAC" in name.upper():
        return "tlac"

    # 永续债：优先用含权期限说明字段（可识别名称无标记的永续债，如 26潞安MTN003B）
    if _is_perpetual_by_field(cvtbd_expire):
        return "perpetual"

    # 名称兜底：名称含"永续" 或 尾部为 Y+数字
    if "永续" in name:
        return "perpetual"
    if _PERPETUAL_SUFFIX_RE.search(name):
        return "perpetual"

    return "ordinary"


def is_exchange_kechuang_bond(bond_name: str, exchange: Optional[str]) -> bool:
    """Return whether a bond is an exchange-listed Kechuang bond for pricing."""
    return (
        "K" in str(bond_name or "").upper()
        and str(exchange or "").strip() in config.EXCHANGE_BOND_EXCHANGES
    )


def is_broker_subordinated_bond(issuer: str, bond_name: str) -> bool:
    """Return whether a bond is a broker subordinated bond.

    财汇中同一 SYMBOL 可能有多条交易场所记录，不能用 EXCHANGE 作为
    唯一识别依据。券商次级债按业务口径识别为证券发行人发行的、简称以
    C 加数字结尾的债券；例如 ``26兴业C2``。尾部限制避免将 ``证券CP001``
    等短融误判为次级债。
    """
    return (
        "证券" in str(issuer or "")
        and bool(_BROKER_SUBORDINATED_SUFFIX_RE.search(str(bond_name or "").strip()))
    )


def interpolate_curve(term: float, curve_points: list[tuple[float, float]]) -> Optional[float]:
    """
    线性插值获取曲线上某期限的收益率值

    Args:
        term: 目标期限（年）
        curve_points: [(maturity, yield), ...] 已按maturity排序

    Returns:
        插值后的收益率，或None（曲线为空时）
    """
    if not curve_points:
        return None

    tenors = [p[0] for p in curve_points]
    yields = [p[1] for p in curve_points]

    if term <= tenors[0]:
        return yields[0]
    if term >= tenors[-1]:
        return yields[-1]

    for i in range(len(tenors) - 1):
        if tenors[i] <= term <= tenors[i + 1]:
            if tenors[i + 1] == tenors[i]:
                return yields[i]
            ratio = (term - tenors[i]) / (tenors[i + 1] - tenors[i])
            return yields[i] + ratio * (yields[i + 1] - yields[i])

    return None


def get_curve_code_for_rating(rating: str) -> str:
    """根据发行人评级获取对应的曲线代码"""
    if not rating:
        return "216"  # 默认AA+
    rating_upper = rating.strip().upper()
    return config.RATING_CURVE_CODES.get(rating_upper, "216")


# ──────────────────────────────────────────────────────
# 带缓存的曲线查询（消除重复查询 + 替换慢MAX）
# ──────────────────────────────────────────────────────

class _QueryCache:
    """
    查询缓存（可跨发行人共享）。

    核心优化（参考 juyuan_credit_tools_portal）：
    - 估值数据按日期切片全量缓存：同一日期只查一次数据库，读取全部SYMBOL
    - 多个发行人的存续债如果在同一天，直接从内存字典取值
    - 曲线数据按(curve_code, date)缓存
    """

    def __init__(self, conn):
        self.conn = conn
        self._curve_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self._curve_date_cache: dict[tuple[str, str], Optional[str]] = {}
        # 日期切片缓存：{date_str: {symbol: {"yield": float, "term": float}}}
        self._date_slice_cache: dict[str, dict[str, dict]] = {}
        # 隐含评级缓存：{secode: implied_rating_str}
        self._implied_rating_cache: dict[str, str] = {}
        self._curve_available_dates: dict[tuple[str, str, str], set[str]] = {}

    def get_curve(self, curve_code: str, trade_date: str) -> list[tuple[float, float]]:
        """缓存曲线查询"""
        key = (curve_code, trade_date)
        if key not in self._curve_cache:
            self._curve_cache[key] = fetch_curve(self.conn, curve_code, trade_date)
        return self._curve_cache[key]

    def preload_curve_dates(self, target_dates: list[str]) -> None:
        """Preload nearest curve dates for configured rating curves."""
        normalized_dates = sorted({str(d)[:8] for d in target_dates if d})
        if not normalized_dates:
            return

        curve_codes = sorted(set(config.RATING_CURVE_CODES.values()) | {"216"})
        cur = self.conn.cursor()

        for curve_code in curve_codes:
            for target_date in normalized_dates:
                target_dt = datetime.strptime(target_date, "%Y%m%d")
                for delta in range(31):
                    check_date = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")
                    cur.execute("""
                        SELECT /*+ INDEX(t) */ 1
                        FROM TQ_QT_YIELDCURVE t
                        WHERE t.TRADEDATE = :d
                          AND t.YCURVECODE = :c
                          AND t.YCURVETYPE = '1'
                          AND t.ISVALID = 1
                          AND t.MATURITY = 1
                          AND ROWNUM = 1
                    """, {"d": check_date, "c": curve_code})
                    if cur.fetchone():
                        self._curve_date_cache[(curve_code, target_date)] = check_date
                        break

    def get_nearest_curve_date(self, target_date: str, curve_code: str) -> Optional[str]:
        """
        用逐日回退替代慢MAX查询
        策略：直接查目标日→回退1天→...→最多回退7天
        每次查询 ~60ms，7次最多 ~420ms（vs MAX的1489ms）
        """
        key = (curve_code, target_date)
        if key in self._curve_date_cache:
            return self._curve_date_cache[key]

        target_dt = datetime.strptime(target_date[:8], "%Y%m%d")
        cur = self.conn.cursor()

        for delta in range(31):
            check_date = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")
            cur.execute("""
                SELECT /*+ INDEX(t) */ 1
                FROM TQ_QT_YIELDCURVE t
                WHERE t.TRADEDATE = :d
                  AND t.YCURVECODE = :c
                  AND t.YCURVETYPE = '1'
                  AND t.ISVALID = 1
                  AND t.MATURITY = 1
                  AND ROWNUM = 1
            """, {"d": check_date, "c": curve_code})
            if cur.fetchone():
                self._curve_date_cache[key] = check_date
                return check_date

        self._curve_date_cache[key] = None
        return None

        cur = self.conn.cursor()
        target_dt = datetime.strptime(target_date[:8], "%Y%m%d")

        for delta in range(8):  # 最多回退7天
            check_date = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")
            cur.execute("""
                SELECT COUNT(*) FROM TQ_QT_YIELDCURVE
                WHERE TRADEDATE = :d AND YCURVECODE = :c 
                  AND YCURVETYPE = '1' AND ISVALID = 1 AND MATURITY = 1
            """, {"d": check_date, "c": curve_code})
            if cur.fetchone()[0] > 0:
                self._curve_date_cache[key] = check_date
                return check_date

        self._curve_date_cache[key] = None
        return None

    def _load_date_slice(self, date_str: str) -> dict[str, dict]:
        """
        加载某日期的全量估值数据到内存（日期切片策略）。

        核心原理（参考 juyuan_credit_tools_portal/db.py fetch_shclest_yields）：
        BESTIMATE 表在 TDATE 列上有索引，读取单日全量是一次高效索引扫描。
        加载后不同发行人的不同 symbols 均可从内存字典 O(1) 查找。

        注意：该方法读取约 10 万行/日，对少量 symbols 场景效率低下。
        优先使用 get_valuations_batch（IN 查询策略）。
        """
        if date_str not in self._date_slice_cache:
            self._date_slice_cache[date_str] = fetch_all_valuations_for_date(
                self.conn, date_str
            )
        return self._date_slice_cache[date_str]

    def _fetch_symbols_for_date(self, symbols: list[str], date_str: str) -> dict[str, dict]:
        """
        IN 查询策略：一条 SQL 查询指定 symbols 在指定日期的估值。

        性能实测（25只 symbols + 1个日期）：
        - IN 查询: ~29ms（1次网络往返）
        - 全量日期切片: ~250ms（读取 ~10万行后 Python 过滤）
        - 逐只点查: ~2900ms（25次网络往返 × ~117ms/次）

        IN 查询比全量切片快约 8x，比逐只点查快约 100x。
        """
        if not symbols:
            return {}

        cur = self.conn.cursor()
        results = {}
        batch_size = 800  # Oracle IN 子句安全上限

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            placeholders = ",".join(f":s{j}" for j in range(len(batch)))
            params = {f"s{j}": s for j, s in enumerate(batch)}
            params["d"] = date_str

            sql = f"""
                SELECT SYMBOL, YIELD, TERMTOMATURITY
                FROM BESTIMATE
                WHERE SYMBOL IN ({placeholders})
                  AND TDATE = :d
                  AND YIELD IS NOT NULL
                  AND DATASOURCE = '1'
            """
            cur.execute(sql, params)
            for symbol, yld, term in cur.fetchall():
                if yld is not None and term is not None:
                    results[str(symbol)] = {
                        "yield": float(yld),
                        "term": float(term),
                    }

        return results

    def get_valuations_batch(self, symbols: list[str], target_date: str) -> dict[str, dict]:
        """
        批量获取估值，使用 IN 查询 + 逐日回退策略。

        优化历程：
        - v1（原版）：SYMBOL IN (...) 分批 SQL，200个/批 → 多次网络往返
        - v2（日期切片）：读取全日 ~10万行，Python 过滤 → 单次 IO 但数据量大
        - v3（当前）：IN 查询精确命中 + 逐日回退 → 最优

        实测性能（25只存续债场景）：
        - v2 全量切片: ~250ms
        - v3 IN查询回退: ~15ms（快 16x）
        """
        all_results = {}
        remaining = list(set(str(s) for s in symbols if s))
        target_dt = datetime.strptime(target_date[:8], "%Y%m%d")

        for delta in range(6):  # 最多回退5天
            if not remaining:
                break
            check_date = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")

            # IN 查询：精确查目标 symbols（而非全量切片）
            day_results = self._fetch_symbols_for_date(remaining, check_date)

            for sym, val in day_results.items():
                all_results[sym] = val
            remaining = [s for s in remaining if s not in all_results]

        return all_results

    def get_implied_ratings(self, secodes: list[str]) -> dict[str, str]:
        """
        批量获取隐含评级（带缓存）。

        从 TQ_BD_NEWHIDECREDIT 表获取中债隐含评级（STDCREDIT字段），
        参考 juyuan_credit_tools_portal 项目中 implied_rating 的使用方式。

        Args:
            secodes: 证券内码列表

        Returns:
            {secode: implied_rating_str} 字典
        """
        # 找出还未缓存的secode
        uncached = [s for s in secodes if s not in self._implied_rating_cache]
        if uncached:
            new_ratings = fetch_implied_ratings(self.conn, uncached)
            self._implied_rating_cache.update(new_ratings)
            # 对未查到的secode也缓存空字符串，避免重复查库
            for s in uncached:
                if s not in self._implied_rating_cache:
                    self._implied_rating_cache[s] = ""

        return {s: self._implied_rating_cache.get(s, "") for s in secodes}


# ──────────────────────────────────────────────────────
# 单只债券偏离计算（使用缓存版）
# ──────────────────────────────────────────────────────

def _calculate_single_bond_with_cache(
    cache: _QueryCache,
    bond_symbol: str,
    bond_name: str,
    issuer: str,
    coupon_rate: Optional[float],
    issue_date: str,
    maturity_year: float,
    rating: str,
    raise_mode: str,
    outstanding: pd.DataFrame,
    valuations: dict[str, dict],
    cvtbd_expire: Optional[str] = None,
    exchange: Optional[str] = None,
    issue_amount_wan: Optional[float] = None,
) -> Optional[dict]:
    """
    计算单只债券偏离（使用预查询的存续债和估值数据）

    Args:
        cache: 查询缓存对象
        outstanding: 已查好的存续债列表
        valuations: 已查好的存续债估值数据
        cvtbd_expire: 新发债的含权期限说明（CVTBDEXPIREMEMP），用于永续债识别
        其余参数同 calculate_single_bond_deviation
    """
    # 排除自身
    out_filtered = outstanding[outstanding["SYMBOL"] != bond_symbol]
    if out_filtered.empty:
        return None

    if not valuations:
        return None

    # 公募债只与公募债比较，私募/定向债只与私募/定向债比较。
    raise_mode = str(raise_mode or "").strip()
    if raise_mode and "RAISEMODE" in out_filtered.columns:
        out_filtered = out_filtered[out_filtered["RAISEMODE"].astype(str).str.strip() == raise_mode]
        if out_filtered.empty:
            return None

    # ── 同品种过滤：永续债/二级资本债/TLAC只能和同类比较 ──
    # 品种识别优先使用含权期限说明字段（CVTBDEXPIREMEMP），
    # 可识别名称无永续标记的永续债（如 26潞安MTN003B），名称规则作兜底。
    my_type = classify_bond_type(bond_name, cvtbd_expire)
    if is_broker_subordinated_bond(issuer, bond_name):
        my_type = "broker_subordinated"

    if "CVTBDEXPIREMEMP" in out_filtered.columns:
        out_types = out_filtered.apply(
            lambda r: classify_bond_type(r["BONDSNAME"], r["CVTBDEXPIREMEMP"]),
            axis=1,
        )
    else:
        # 兼容不含新字段的旧数据（如旧缓存），退化为纯名称识别
        out_types = out_filtered["BONDSNAME"].apply(classify_bond_type)

    candidate_is_broker_subordinated = out_filtered["BONDSNAME"].map(
        lambda name: is_broker_subordinated_bond(issuer, name)
    )
    out_types = out_types.mask(
        candidate_is_broker_subordinated, "broker_subordinated"
    )

    if my_type != "ordinary":
        # 特殊品种：只保留同类存续债
        out_filtered = out_filtered[out_types == my_type]
        if out_filtered.empty:
            return None
    else:
        # 普通债：排除特殊品种存续债
        out_filtered = out_filtered[out_types == "ordinary"]
        if out_filtered.empty:
            return None

    # 私募交易所科创债优先使用剩余期限相差不超过 2 年的其他私募
    # 交易所科创债。无此候选时，只能回退到其他私募普通债；两者都
    # 不可用时不作定价判断。公募交易所科创债沿用原有选择逻辑。
    target_term = float(maturity_year)
    is_kechuang = is_exchange_kechuang_bond(bond_name, exchange)
    is_private_kechuang = is_kechuang and raise_mode == "2"
    kechuang_fallback = False
    ordinary_kechuang_fallback = False

    # Normal credit bonds use exchange Kechuang bonds only as a last resort.
    # Existing logic for Kechuang targets and special bond types stays unchanged.
    if my_type == "ordinary" and not is_kechuang and "EXCHANGE" in out_filtered.columns:
        candidate_is_kechuang = out_filtered.apply(
            lambda r: is_exchange_kechuang_bond(r["BONDSNAME"], r["EXCHANGE"]),
            axis=1,
        )
        has_valuation = out_filtered["SYMBOL"].map(lambda symbol: symbol in valuations)
        non_kechuang_candidates = out_filtered[~candidate_is_kechuang & has_valuation]
        if not non_kechuang_candidates.empty:
            out_filtered = non_kechuang_candidates
        else:
            out_filtered = out_filtered[candidate_is_kechuang & has_valuation]
            ordinary_kechuang_fallback = not out_filtered.empty

    if is_private_kechuang and "EXCHANGE" in out_filtered.columns:
        candidate_is_kechuang = out_filtered.apply(
            lambda r: is_exchange_kechuang_bond(r["BONDSNAME"], r["EXCHANGE"]),
            axis=1,
        )
        nearby_kechuang = out_filtered[candidate_is_kechuang].copy()
        nearby_kechuang = nearby_kechuang[
            nearby_kechuang["SYMBOL"].map(
                lambda symbol: (
                    symbol in valuations
                    and abs(valuations[symbol]["term"] - target_term)
                    <= config.KECHUANG_NEARBY_TERM_YEARS
                )
            )
        ]
        if not nearby_kechuang.empty:
            out_filtered = nearby_kechuang
        else:
            # 私募科创债不能使用期限较远的科创债。改用有估值的私募
            # 普通债；若不存在，则由调用方保留为无判断发行记录。
            ordinary_candidates = out_filtered[
                ~candidate_is_kechuang
                & out_filtered["SYMBOL"].map(lambda symbol: symbol in valuations)
            ]
            if ordinary_candidates.empty:
                return None
            out_filtered = ordinary_candidates
    elif is_kechuang and "EXCHANGE" in out_filtered.columns:
        # 公募交易所科创债保持原有逻辑：优先近期限科创债，否则在全部
        # 同口径候选债中按期限选择。
        candidate_is_kechuang = out_filtered.apply(
            lambda r: is_exchange_kechuang_bond(r["BONDSNAME"], r["EXCHANGE"]),
            axis=1,
        )
        nearby_kechuang = out_filtered[candidate_is_kechuang].copy()
        nearby_kechuang = nearby_kechuang[
            nearby_kechuang["SYMBOL"].map(
                lambda symbol: (
                    symbol in valuations
                    and abs(valuations[symbol]["term"] - target_term)
                    <= config.KECHUANG_NEARBY_TERM_YEARS
                )
            )
        ]
        if not nearby_kechuang.empty:
            out_filtered = nearby_kechuang

    # 批量获取存续债的中债隐含评级（从 TQ_BD_NEWHIDECREDIT 表）
    secodes_in_filter = out_filtered["SECODE"].tolist()
    implied_ratings_map = cache.get_implied_ratings(secodes_in_filter)

    # 找最接近目标期限的存续债
    best_ref = None
    min_diff = 999.0

    for _, row in out_filtered.iterrows():
        symbol = row["SYMBOL"]
        if symbol not in valuations:
            continue
        val_data = valuations[symbol]
        ref_term = val_data["term"]
        diff = abs(ref_term - target_term)
        if diff < min_diff:
            min_diff = diff
            # 隐含评级通过SECODE从TQ_BD_NEWHIDECREDIT获取
            secode = str(row["SECODE"])
            ref_start_date = row["STARTDATE"] if "STARTDATE" in row.index else ""
            best_ref = {
                "name": row["BONDSNAME"],
                "symbol": symbol,
                "start_date": str(ref_start_date) if pd.notna(ref_start_date) else "",
                "yield": val_data["yield"],
                "term": ref_term,
                "implied_rating": implied_ratings_map.get(secode, ""),
                "is_exchange_kechuang_bond": is_exchange_kechuang_bond(
                    row["BONDSNAME"], row["EXCHANGE"] if "EXCHANGE" in row.index else None
                ),
            }

    if not best_ref:
        return None

    # 未找到相近期限科创债时，若最终最近期限参考债为普通债，则合理收益率下调 5BP。
    kechuang_fallback = is_kechuang and not best_ref["is_exchange_kechuang_bond"]

    # 获取评级曲线（带缓存）
    # 优先使用参考债券的中债隐含评级（来自TQ_BD_NEWHIDECREDIT.STDCREDIT），
    # 参考 juyuan_credit_tools_portal 项目的 curve_for_bond() 逻辑；
    # 隐含评级不可用时回退到 NEWISSUERATE（发行时评级）。
    implied_rating = best_ref.get("implied_rating", "")
    effective_rating = implied_rating if implied_rating else rating
    curve_code = get_curve_code_for_rating(effective_rating)
    curve_date = cache.get_nearest_curve_date(issue_date, curve_code)
    if not curve_date:
        return None

    curve_points = cache.get_curve(curve_code, curve_date)
    if not curve_points:
        return None

    # 曲线插值
    curve_at_ref = interpolate_curve(best_ref["term"], curve_points)
    curve_at_target = interpolate_curve(target_term, curve_points)

    if curve_at_ref is None or curve_at_target is None:
        return None

    # 计算偏离
    spread = best_ref["yield"] - curve_at_ref
    fair_price = curve_at_target + spread
    if kechuang_fallback:
        fair_price += config.KECHUANG_FALLBACK_FAIR_PRICE_ADJUSTMENT
    if ordinary_kechuang_fallback:
        fair_price += config.ORDINARY_KECHUANG_FALLBACK_FAIR_PRICE_ADJUSTMENT
    has_coupon = coupon_rate is not None and pd.notna(coupon_rate) and float(coupon_rate) > 0
    deviation = float(coupon_rate) - fair_price if has_coupon else None
    deviation_bp = deviation * 100 if deviation is not None else None
    ref_start_date = best_ref.get("start_date", "")
    ref_date_gap_years = _date_gap_years(issue_date, ref_start_date)
    is_no_judgement = (
        ref_date_gap_years is not None
        and ref_date_gap_years > NO_JUDGEMENT_DATE_GAP_YEARS
    )

    return {
        "bond_symbol": bond_symbol,
        "bond_name": bond_name,
        "bond_type": my_type,
        "is_exchange_kechuang_bond": is_kechuang,
        "kechuang_fallback_to_ordinary": kechuang_fallback,
        "ordinary_fallback_to_exchange_kechuang": ordinary_kechuang_fallback,
        "cvtbd_expire": (
            str(cvtbd_expire).strip()
            if cvtbd_expire is not None and pd.notna(cvtbd_expire) and str(cvtbd_expire).strip()
            else ""
        ),
        "raise_mode": raise_mode,
        "issuer": issuer,
        "coupon_rate": round(float(coupon_rate), 4) if has_coupon else None,
        "issue_amount_wan": (
            round(float(issue_amount_wan), 4)
            if issue_amount_wan is not None and pd.notna(issue_amount_wan)
            else None
        ),
        "issue_date": issue_date,
        "maturity_year": round(maturity_year, 4),
        "rating": rating,
        "implied_rating": implied_rating,
        "effective_rating": effective_rating,
        "ref_bond_name": best_ref["name"],
        "ref_bond_symbol": best_ref["symbol"],
        "ref_start_date": ref_start_date,
        "ref_date_gap_years": round(ref_date_gap_years, 4) if ref_date_gap_years is not None else None,
        "ref_yield": round(best_ref["yield"], 4),
        "ref_term": round(best_ref["term"], 4),
        "curve_code": curve_code,
        "curve_at_ref": round(curve_at_ref, 4),
        "curve_at_target": round(curve_at_target, 4),
        "spread": round(spread, 4),
        "fair_price": round(fair_price, 4),
        "deviation": round(deviation, 4) if deviation is not None else None,
        "deviation_bp": round(deviation_bp, 2) if deviation_bp is not None else None,
        "is_non_market": False if is_no_judgement or deviation_bp is None else deviation_bp < -config.DEVIATION_THRESHOLD_BP,
        "is_overpriced": False if is_no_judgement or deviation_bp is None else deviation_bp > config.DEVIATION_THRESHOLD_BP,
        "is_no_judgement": is_no_judgement or deviation_bp is None,
    }


# ──────────────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────────────

def calculate_single_bond_deviation(
    conn,
    bond_symbol: str,
    bond_name: str,
    issuer: str,
    coupon_rate: Optional[float],
    issue_date: str,
    maturity_year: float,
    rating: str,
    raise_mode: str = "",
    cvtbd_expire: Optional[str] = None,
    exchange: Optional[str] = None,
) -> Optional[dict]:
    """
    计算单只债券的一级发行偏离度（独立调用版，不带缓存）
    用于测试或单只债券计算场景。

    Args:
        cvtbd_expire: 含权期限说明（CVTBDEXPIREMEMP），如 "3+N"，用于永续债识别
    """
    outstanding = fetch_issuer_outstanding(conn, issuer, issue_date)
    if outstanding.empty:
        return None

    symbols = outstanding["SYMBOL"].tolist()
    cache = _QueryCache(conn)
    valuations = cache.get_valuations_batch(symbols, issue_date)

    return _calculate_single_bond_with_cache(
        cache=cache,
        bond_symbol=bond_symbol,
        bond_name=bond_name,
        issuer=issuer,
        coupon_rate=coupon_rate,
        issue_date=issue_date,
        maturity_year=maturity_year,
        rating=rating,
        raise_mode=raise_mode,
        outstanding=outstanding,
        valuations=valuations,
        cvtbd_expire=cvtbd_expire,
        exchange=exchange,
    )


def calculate_issuer_deviations(
    conn,
    issuer: str,
    start_date: str = "20240101",
    end_date: str = None,
    exclude_short_term: bool = False,
    shared_cache: Optional[_QueryCache] = None,
) -> dict:
    """
    计算某发行人的所有一级债券偏离情况（优化版）

    性能优化点：
    1. 存续债列表按发行日分组，相近日期共用一次查询
    2. 估值数据按日期批量查，用逐日回退替代MAX
    3. 曲线数据全程缓存，不重复查

    Returns:
        {
            "issuer": str,
            "total_bonds": int,
            "calculated_bonds": int,
            "non_market_count": int,
            "non_market_ratio": float,
            "bonds": [每只债券的偏离详情dict],
            "avg_deviation_bp": float,
        }
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    # 获取该发行人的所有新发债券
    issuer_issues = fetch_new_issues(conn, start_date, end_date, issuer=issuer)

    if exclude_short_term:
        issuer_issues = issuer_issues[issuer_issues["EFFECTIVE_TERM"] >= 1.0]

    if issuer_issues.empty:
        return {
            "issuer": issuer,
            "total_bonds": 0,
            "calculated_bonds": 0,
            "non_market_count": 0,
            "non_market_ratio": 0.0,
            "overpriced_count": 0,
            "overpriced_ratio": 0.0,
            "bonds": [],
            "avg_deviation_bp": 0.0,
        }

    # 使用外部共享缓存（跨发行人复用曲线/隐含评级），或新建独立缓存
    cache = shared_cache if shared_cache is not None else _QueryCache(conn)

    # ── 优化核心：按发行日分组，相同日期共用存续债和估值 ──
    issuer_issues["_issue_date_str"] = issuer_issues["ISSUE_DATE"].astype(str)
    grouped = issuer_issues.groupby("_issue_date_str")

    results = []
    for issue_date, group_df in grouped:
        # 每个发行日只查一次存续债
        outstanding = fetch_issuer_outstanding(conn, issuer, issue_date)
        if outstanding.empty:
            continue

        # 批量估值（带逐日回退缓存）
        symbols = outstanding["SYMBOL"].tolist()
        valuations = cache.get_valuations_batch(symbols, issue_date)
        cache.get_implied_ratings([str(s) for s in outstanding["SECODE"].dropna().tolist()])

        # 该日期下所有新发债逐只计算（共享存续债+估值）
        for _, row in group_df.iterrows():
            result = _calculate_single_bond_with_cache(
                cache=cache,
                bond_symbol=row["SYMBOL"],
                bond_name=row["BONDSNAME"],
                issuer=issuer,
                coupon_rate=float(row["COUPONRATE"]) if pd.notna(row["COUPONRATE"]) else None,
                issue_date=str(row["ISSUE_DATE"]),
                maturity_year=float(row["EFFECTIVE_TERM"]),
                rating=str(row["RATING"]) if row["RATING"] else "",
                raise_mode=str(row["RAISEMODE"]) if row["RAISEMODE"] else "",
                outstanding=outstanding,
                valuations=valuations,
                cvtbd_expire=row["CVTBDEXPIREMEMP"] if "CVTBDEXPIREMEMP" in row.index else None,
                exchange=row["EXCHANGE"] if "EXCHANGE" in row.index else None,
                issue_amount_wan=(
                    float(row["ISSUE_AMOUNT_WAN"])
                    if "ISSUE_AMOUNT_WAN" in row.index and pd.notna(row["ISSUE_AMOUNT_WAN"])
                    else None
                ),
            )
            if result:
                results.append(result)

    # 定价参考缺失不应让债券从发行统计中消失；保留基础发行事实并标记无判断。
    result_symbols = {str(result.get("bond_symbol") or "") for result in results}
    for _, row in issuer_issues.iterrows():
        symbol = str(row["SYMBOL"])
        if symbol in result_symbols:
            continue
        cvtbd_expire = row["CVTBDEXPIREMEMP"] if "CVTBDEXPIREMEMP" in row.index else None
        bond_name = str(row["BONDSNAME"] or "")
        issue_amount = row["ISSUE_AMOUNT_WAN"] if "ISSUE_AMOUNT_WAN" in row.index else None
        results.append({
            "bond_symbol": symbol,
            "bond_name": bond_name,
            "bond_type": classify_bond_type(bond_name, cvtbd_expire),
            "cvtbd_expire": str(cvtbd_expire).strip() if pd.notna(cvtbd_expire) else "",
            "raise_mode": str(row["RAISEMODE"] or ""),
            "issuer": issuer,
            "coupon_rate": float(row["COUPONRATE"]) if pd.notna(row["COUPONRATE"]) else None,
            "issue_amount_wan": float(issue_amount) if pd.notna(issue_amount) else None,
            "issue_date": str(row["ISSUE_DATE"]),
            "maturity_year": round(float(row["EFFECTIVE_TERM"]), 4),
            "rating": str(row["RATING"] or ""),
            "implied_rating": "",
            "effective_rating": "",
            "ref_bond_name": "",
            "ref_bond_symbol": "",
            "ref_start_date": "",
            "ref_date_gap_years": None,
            "ref_yield": None,
            "ref_term": None,
            "curve_code": "",
            "curve_at_ref": None,
            "curve_at_target": None,
            "spread": None,
            "fair_price": None,
            "deviation": None,
            "deviation_bp": None,
            "is_non_market": False,
            "is_overpriced": False,
            "is_no_judgement": True,
        })

    # 汇总统计：日期差异过大的债券保留展示，但不纳入有效判定统计。
    valid_results = [
        r for r in results
        if not r.get("is_no_judgement") and r.get("deviation_bp") is not None
    ]
    non_market_count = sum(1 for r in valid_results if r["is_non_market"])
    overpriced_count = sum(1 for r in valid_results if r["is_overpriced"])
    total_calculated = len(valid_results)
    non_market_ratio = non_market_count / total_calculated if total_calculated > 0 else 0.0
    overpriced_ratio = overpriced_count / total_calculated if total_calculated > 0 else 0.0
    avg_deviation = sum(r["deviation_bp"] for r in valid_results) / total_calculated if total_calculated > 0 else 0.0

    return {
        "issuer": issuer,
        "total_bonds": len(issuer_issues),
        "calculated_bonds": total_calculated,
        "non_market_count": non_market_count,
        "non_market_ratio": round(non_market_ratio, 4),
        "overpriced_count": overpriced_count,
        "overpriced_ratio": round(overpriced_ratio, 4),
        "bonds": results,
        "avg_deviation_bp": round(avg_deviation, 2),
    }


def calculate_all_issuers(
    conn,
    start_date: str = "20240101",
    end_date: str = None,
    exclude_short_term: bool = False,
    progress_callback=None,
) -> list[dict]:
    """
    计算所有发行人的非市场化发行情况

    Returns:
        [{issuer_summary_dict}, ...]
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    from .data_fetcher import get_all_issuers
    issuers = get_all_issuers(conn, start_date, end_date)

    all_results = []
    for i, issuer_name in enumerate(issuers):
        if progress_callback:
            progress_callback(i + 1, len(issuers), issuer_name)

        try:
            result = calculate_issuer_deviations(
                conn, issuer_name, start_date, end_date, exclude_short_term
            )
            if result["calculated_bonds"] > 0:
                all_results.append(result)
        except Exception as e:
            print(f"  ⚠️ 计算 {issuer_name} 时出错: {e}")
            continue

    return all_results

