"""
一级发行非市场化评估系统 - 缓存构建脚本

策略：按发行日期倒序构建缓存
    - 利用 Oracle buffer cache 特性：同一日期的所有发行人共享热缓存
    - 用户最近日期优先可用
    - 支持断点续建、后台持续运行、优雅退出

运行：python cache_builder.py
    可选参数：
        --start 20240101     起始日期（默认20240101）
        --end   20260706     截止日期（默认今天）
        --reset              清除已有缓存重新构建
        --once               只跑一轮不循环
"""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    # Keep progress logging from aborting on legacy Windows GBK consoles.
    sys.stdout.reconfigure(errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from . import config
from .db_utils import get_connection

# 项目根加入 sys.path，复用 paths.py 统一数据路径（PORTAL_DATA_ROOT 定位）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
from paths import PRIMARY_PRICING_CACHE as CACHE_DB_PATH

# ──────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────

ISSUE_DATE_RULE_VERSION = "issue_date_v7_broker_subordinated"  # v7: 券商次级债按证券发行人+简称尾部C数字识别
HISTORY_START_DATE = "20240101"

# 优雅退出信号
_STOP_SIGNAL = False


def _signal_handler(signum, frame):
    global _STOP_SIGNAL
    _STOP_SIGNAL = True
    print("\n⏸️  收到退出信号，当前发行人处理完后安全退出...")


# ──────────────────────────────────────────────────────
# SQLite 缓存数据库初始化（app.py 调用入口）
# ──────────────────────────────────────────────────────

def init_cache_db(db_path: str = None) -> sqlite3.Connection:
    """
    初始化缓存数据库（创建表和索引）。

    供 app.py 的 _write_to_cache 调用：
        from cache_builder import init_cache_db, save_issuer_result
        cache_conn = init_cache_db(CACHE_DB_PATH)
        save_issuer_result(cache_conn, result, start_date, end_date)
    """
    if db_path is None:
        db_path = CACHE_DB_PATH

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bond_deviations (
            symbol TEXT NOT NULL,
            issue_date_rule TEXT NOT NULL,
            bond_name TEXT,
            issuer TEXT,
            coupon_rate REAL,
            issue_amount_wan REAL,
            issue_date TEXT,
            effective_term REAL,
            rating TEXT,
            implied_rating TEXT,
            effective_rating TEXT,
            raise_mode TEXT,
            bond_type TEXT,
            cvtbd_expire TEXT,
            ref_bond_name TEXT,
            ref_bond_symbol TEXT,
            ref_start_date TEXT,
            ref_date_gap_years REAL,
            ref_yield REAL,
            ref_term REAL,
            curve_code TEXT,
            curve_at_ref REAL,
            curve_at_target REAL,
            spread REAL,
            fair_price REAL,
            deviation REAL,
            deviation_bp REAL,
            is_non_market INTEGER,
            is_overpriced INTEGER,
            is_no_judgement INTEGER DEFAULT 0,
            computed_at TEXT,
            PRIMARY KEY (symbol, issue_date_rule)
        );

        CREATE TABLE IF NOT EXISTS issuer_summary (
            issuer TEXT NOT NULL,
            issue_date_rule TEXT NOT NULL,
            total_bonds INTEGER,
            calculated_bonds INTEGER,
            non_market_count INTEGER,
            non_market_ratio REAL,
            overpriced_count INTEGER,
            overpriced_ratio REAL,
            avg_deviation_bp REAL,
            start_date TEXT,
            end_date TEXT,
            last_updated TEXT,
            PRIMARY KEY (issuer, issue_date_rule)
        );

        CREATE TABLE IF NOT EXISTS run_meta (
            id INTEGER PRIMARY KEY DEFAULT 1,
            last_start_date TEXT,
            last_end_date TEXT,
            last_run_time TEXT,
            total_issuers INTEGER,
            completed_issuers INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_bond_issuer
            ON bond_deviations(issuer);
        CREATE INDEX IF NOT EXISTS idx_bond_issue_date
            ON bond_deviations(issue_date);
        CREATE INDEX IF NOT EXISTS idx_bond_issuer_rule_date
            ON bond_deviations(issuer, issue_date_rule, issue_date);
        CREATE INDEX IF NOT EXISTS idx_bond_rule_date_issuer_symbol
            ON bond_deviations(issue_date_rule, issue_date, issuer, symbol);
        CREATE INDEX IF NOT EXISTS idx_summary_rule_issuer
            ON issuer_summary(issue_date_rule, issuer);
    """)
    conn.commit()

    # Schema migration: add missing columns to legacy tables
    _migrate_schema(conn)

    return conn


def _migrate_schema(conn: sqlite3.Connection):
    """为旧版 cache.db 添加缺失字段（兼容升级）"""
    cur = conn.cursor()

    # bond_deviations 可能缺失的列
    cur.execute("PRAGMA table_info(bond_deviations)")
    existing_cols = {row[1] for row in cur.fetchall()}

    new_cols = {
        "is_no_judgement": "INTEGER DEFAULT 0",
        "is_overpriced": "INTEGER DEFAULT 0",
        "ref_start_date": "TEXT",
        "ref_date_gap_years": "REAL",
        "implied_rating": "TEXT",
        "effective_rating": "TEXT",
        "raise_mode": "TEXT",
        "bond_type": "TEXT",
        "cvtbd_expire": "TEXT",
        "issue_amount_wan": "REAL",
    }
    for col_name, col_def in new_cols.items():
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE bond_deviations ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists

    # issuer_summary 可能缺失的列
    cur.execute("PRAGMA table_info(issuer_summary)")
    existing_summary_cols = {row[1] for row in cur.fetchall()}

    summary_new_cols = {
        "overpriced_count": "INTEGER DEFAULT 0",
        "overpriced_ratio": "REAL DEFAULT 0",
        "issue_date_rule": "TEXT",
    }
    for col_name, col_def in summary_new_cols.items():
        if col_name not in existing_summary_cols:
            try:
                conn.execute(f"ALTER TABLE issuer_summary ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass

    conn.commit()


# ──────────────────────────────────────────────────────
# 保存单个发行人结果（app.py 调用入口）
# ──────────────────────────────────────────────────────

def save_issuer_result(
    cache_conn: sqlite3.Connection,
    result: dict,
    start_date: str,
    end_date: str,
):
    """
    将一个发行人的计算结果写入 SQLite 缓存。

    Args:
        cache_conn: SQLite 连接
        result: calculate_issuer_deviations 的返回值
        start_date: 计算起始日期
        end_date: 计算截止日期
    """
    issuer = result.get("issuer", "")
    if not issuer:
        return

    bonds = result.get("bonds", [])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = cache_conn.execute(
        """
        SELECT start_date, end_date
        FROM issuer_summary
        WHERE issuer = ? AND issue_date_rule = ?
        """,
        (issuer, ISSUE_DATE_RULE_VERSION),
    ).fetchone()
    merged_start_date = min(
        [d for d in [start_date, existing[0] if existing else None] if d]
    )
    merged_end_date = max(
        [d for d in [end_date, existing[1] if existing else None] if d]
    )

    # 写入 bond_deviations
    for bond in bonds:
        cache_conn.execute("""
            INSERT OR REPLACE INTO bond_deviations (
                symbol, issue_date_rule, bond_name, issuer, coupon_rate, issue_amount_wan,
                issue_date, effective_term, rating, implied_rating,
                effective_rating, raise_mode, bond_type, cvtbd_expire,
                ref_bond_name, ref_bond_symbol, ref_start_date,
                ref_date_gap_years, ref_yield, ref_term,
                curve_code, curve_at_ref, curve_at_target,
                spread, fair_price, deviation, deviation_bp,
                is_non_market, is_overpriced, is_no_judgement, computed_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?
            )
        """, (
            bond.get("bond_symbol", ""),
            ISSUE_DATE_RULE_VERSION,
            bond.get("bond_name", ""),
            issuer,
            bond.get("coupon_rate"),
            bond.get("issue_amount_wan"),
            bond.get("issue_date", ""),
            bond.get("maturity_year"),
            bond.get("rating", ""),
            bond.get("implied_rating", ""),
            bond.get("effective_rating", ""),
            bond.get("raise_mode", ""),
            bond.get("bond_type", ""),
            bond.get("cvtbd_expire", ""),
            bond.get("ref_bond_name", ""),
            bond.get("ref_bond_symbol", ""),
            bond.get("ref_start_date", ""),
            bond.get("ref_date_gap_years"),
            bond.get("ref_yield"),
            bond.get("ref_term"),
            bond.get("curve_code", ""),
            bond.get("curve_at_ref"),
            bond.get("curve_at_target"),
            bond.get("spread"),
            bond.get("fair_price"),
            bond.get("deviation"),
            bond.get("deviation_bp"),
            1 if bond.get("is_non_market") else 0,
            1 if bond.get("is_overpriced") else 0,
            1 if bond.get("is_no_judgement") else 0,
            now_str,
        ))

    aggregate = cache_conn.execute(
        """
        SELECT
            COUNT(*) AS total_bonds,
            SUM(CASE
                WHEN COALESCE(is_no_judgement, 0) = 0 AND deviation_bp IS NOT NULL
                THEN 1 ELSE 0
            END) AS calculated_bonds,
            SUM(CASE
                WHEN COALESCE(is_no_judgement, 0) = 0 AND deviation_bp IS NOT NULL
                     AND is_non_market = 1
                THEN 1 ELSE 0
            END) AS non_market_count,
            SUM(CASE
                WHEN COALESCE(is_no_judgement, 0) = 0 AND deviation_bp IS NOT NULL
                     AND is_overpriced = 1
                THEN 1 ELSE 0
            END) AS overpriced_count,
            AVG(CASE
                WHEN COALESCE(is_no_judgement, 0) = 0 AND deviation_bp IS NOT NULL
                THEN deviation_bp ELSE NULL
            END) AS avg_deviation_bp
        FROM bond_deviations
        WHERE issuer = ? AND issue_date_rule = ?
          AND issue_date >= ? AND issue_date <= ?
        """,
        (issuer, ISSUE_DATE_RULE_VERSION, merged_start_date, merged_end_date),
    ).fetchone()
    total_bonds = int(aggregate[0] or 0)
    calculated_bonds = int(aggregate[1] or 0)
    non_market_count = int(aggregate[2] or 0)
    overpriced_count = int(aggregate[3] or 0)
    avg_deviation_bp = round(float(aggregate[4] or 0.0), 2)
    if total_bonds == 0:
        total_bonds = result.get("total_bonds", 0)
        calculated_bonds = result.get("calculated_bonds", 0)
        non_market_count = result.get("non_market_count", 0)
        overpriced_count = result.get("overpriced_count", 0)
        avg_deviation_bp = result.get("avg_deviation_bp", 0.0)

    non_market_ratio = round(non_market_count / calculated_bonds, 4) if calculated_bonds else 0.0
    overpriced_ratio = round(overpriced_count / calculated_bonds, 4) if calculated_bonds else 0.0

    # 写入 issuer_summary
    cache_conn.execute("""
        INSERT OR REPLACE INTO issuer_summary (
            issuer, issue_date_rule,
            total_bonds, calculated_bonds,
            non_market_count, non_market_ratio,
            overpriced_count, overpriced_ratio,
            avg_deviation_bp, start_date, end_date, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        issuer,
        ISSUE_DATE_RULE_VERSION,
        total_bonds,
        calculated_bonds,
        non_market_count,
        non_market_ratio,
        overpriced_count,
        overpriced_ratio,
        avg_deviation_bp,
        merged_start_date,
        merged_end_date,
        now_str,
    ))

    cache_conn.commit()


# ──────────────────────────────────────────────────────
# 日期倒序缓存构建器
# ──────────────────────────────────────────────────────

class DateOrderedCacheBuilder:
    """
    按发行日期倒序构建缓存。

    策略：
    1. 获取日期区间内所有发行人和对应的最新发行日期
    2. 按最近发行日倒序排列发行人（同日期的发行人连续处理）
    3. 同日期的所有发行人共享 Oracle buffer cache
    4. 支持断点续建：已完成的发行人跳过
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        cache_db_path: str = None,
        coupon_refresh_days: int = 7,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.cache_db_path = cache_db_path or CACHE_DB_PATH
        self.coupon_refresh_days = max(0, coupon_refresh_days)
        self.cache_conn = init_cache_db(self.cache_db_path)
        self._stats = {
            "total": 0,
            "completed": 0,
            "skipped": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    def get_completed_issuers(self) -> set[str]:
        """获取已完成缓存构建的发行人集合"""
        cur = self.cache_conn.cursor()
        cur.execute(
            """
            SELECT issuer
            FROM issuer_summary
            WHERE issue_date_rule = ?
              AND start_date <= ?
              AND end_date >= ?
            """,
            (ISSUE_DATE_RULE_VERSION, self.start_date, self.end_date),
        )
        return {row[0] for row in cur.fetchall()}

    def get_cached_ranges(self) -> dict[str, tuple[str, str]]:
        """Return cached coverage ranges by issuer for the current rule."""
        cur = self.cache_conn.cursor()
        cur.execute(
            """
            SELECT issuer, start_date, end_date
            FROM issuer_summary
            WHERE issue_date_rule = ?
            """,
            (ISSUE_DATE_RULE_VERSION,),
        )
        return {
            row[0]: (row[1] or "", row[2] or "")
            for row in cur.fetchall()
        }

    def get_issuers_with_missing_recent_coupons(self) -> dict[str, str]:
        """Return issuers whose recent cached issues still have no coupon.

        New issue coupons are often populated after the issue record itself.  A
        completed cache range must therefore not make those rows permanent.
        The returned date is the earliest missing coupon date to recalculate.
        """
        if not self.coupon_refresh_days:
            return {}

        refresh_start = max(
            self.start_date,
            (datetime.strptime(self.end_date, "%Y%m%d")
             - timedelta(days=self.coupon_refresh_days)).strftime("%Y%m%d"),
        )
        rows = self.cache_conn.execute(
            """
            SELECT issuer, MIN(issue_date) AS first_missing_date
            FROM bond_deviations
            WHERE issue_date_rule = ?
              AND issue_date >= ? AND issue_date <= ?
              AND (coupon_rate IS NULL OR coupon_rate <= 0)
            GROUP BY issuer
            """,
            (ISSUE_DATE_RULE_VERSION, refresh_start, self.end_date),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def extend_issuer_coverage(self, issuer: str, end_date: str) -> None:
        """Extend an issuer's cache coverage when no new issues exist in the gap."""
        self.cache_conn.execute(
            """
            UPDATE issuer_summary
            SET start_date = CASE
                    WHEN start_date IS NULL OR start_date > ? THEN ?
                    ELSE start_date
                END,
                end_date = CASE
                    WHEN end_date IS NULL OR end_date < ? THEN ?
                    ELSE end_date
                END,
                last_updated = ?
            WHERE issuer = ? AND issue_date_rule = ?
            """,
            (
                self.start_date,
                self.start_date,
                end_date,
                end_date,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                issuer,
                ISSUE_DATE_RULE_VERSION,
            ),
        )

    def get_issuers_ordered_by_date(self, oracle_conn) -> list[tuple[str, str]]:
        """
        获取所有发行人，按最近发行日期倒序排列。

        Returns:
            [(issuer_name, latest_issue_date), ...] 按日期倒序
        """
        from .data_fetcher import fetch_new_issues

        print("📋 获取全量新发债券列表...")
        t0 = time.time()
        all_issues = fetch_new_issues(oracle_conn, self.start_date, self.end_date)
        elapsed = time.time() - t0
        print(f"   ✅ 获取到 {len(all_issues)} 只新发债券 ({elapsed:.1f}s)")

        if all_issues.empty:
            return []

        # 按发行人分组，取每个发行人的最近发行日期
        all_issues["_issue_date_str"] = all_issues["ISSUE_DATE"].astype(str)
        issuer_latest = (
            all_issues.groupby("ISSUER")["_issue_date_str"]
            .max()
            .reset_index()
        )
        issuer_latest.columns = ["issuer", "latest_date"]

        # 按最近日期倒序排列（最新日期的发行人先处理）
        issuer_latest = issuer_latest.sort_values("latest_date", ascending=False)

        result = list(zip(issuer_latest["issuer"], issuer_latest["latest_date"]))
        n_dates = all_issues["_issue_date_str"].nunique()
        print(f"   📊 共 {len(result)} 个发行人，覆盖 {n_dates} 个独立发行日")

        return result

    def build(self):
        """执行缓存构建（主入口）"""
        global _STOP_SIGNAL

        print("=" * 60)
        print("🚀 一级发行非市场化评估 - 缓存构建器")
        print(f"   日期范围: {self.start_date} ~ {self.end_date}")
        print(f"   规则版本: {ISSUE_DATE_RULE_VERSION}")
        print(f"   缓存路径: {self.cache_db_path}")
        print("=" * 60)

        # 获取已完成的发行人
        completed = self.get_completed_issuers()
        incomplete_coupons = self.get_issuers_with_missing_recent_coupons()
        print(f"\n📦 已完成缓存: {len(completed)} 个发行人")
        if incomplete_coupons:
            print(f"   ↳ {len(incomplete_coupons)} 个发行人近期票面利率待回填，将增量重算")

        with get_connection() as oracle_conn:
            # 获取排序后的发行人列表
            issuers_ordered = self.get_issuers_ordered_by_date(oracle_conn)
            if not issuers_ordered:
                print("❌ 未找到任何发行人，退出")
                return

            cached_ranges = self.get_cached_ranges()
            extended = 0
            pending = []
            forced_refresh = {}
            for name, latest_date in issuers_ordered:
                missing_coupon_date = incomplete_coupons.get(name)
                if missing_coupon_date:
                    pending.append((name, latest_date))
                    forced_refresh[name] = missing_coupon_date
                    continue
                if name in completed:
                    continue
                cached_start, cached_end = cached_ranges.get(name, ("", ""))
                if cached_start <= self.start_date and cached_end and latest_date <= cached_end:
                    self.extend_issuer_coverage(name, self.end_date)
                    extended += 1
                    continue
                pending.append((name, latest_date))
            if extended:
                self.cache_conn.commit()
                completed = self.get_completed_issuers()

            self._stats["total"] = len(issuers_ordered)
            self._stats["skipped"] = len(issuers_ordered) - len(pending)
            self._stats["completed"] = self._stats["skipped"]

            print(f"\n⏳ 待处理: {len(pending)} 个发行人"
                  f"（跳过 {len(completed)} 个已完成）")
            if extended:
                print(f"   ↳ 其中 {extended} 个发行人无新增发行，已沿用旧缓存并扩展覆盖日期")
            print("-" * 60)

            # 导入计算引擎和共享缓存类
            from .calculator import calculate_issuer_deviations, _QueryCache

            current_date_group = ""
            date_group_count = 0
            shared_cache = None  # 同日期组共享的缓存实例

            for i, (issuer_name, latest_date) in enumerate(pending):
                if _STOP_SIGNAL:
                    print(f"\n⏹️  安全退出（已处理 {i} 个）")
                    break

                # 日期分组标记（视觉分组）+ 共享缓存管理
                if latest_date != current_date_group:
                    if current_date_group:
                        print(f"   └─ 日期 {current_date_group} 完成 ({date_group_count} 个发行人)")
                    current_date_group = latest_date
                    date_group_count = 0
                    # 新日期组：创建新的共享缓存（曲线+隐含评级跨发行人复用）
                    shared_cache = _QueryCache(oracle_conn)
                    print(f"\n📅 发行日期: {latest_date}")

                date_group_count += 1
                progress = self._stats["completed"] + 1
                total = self._stats["total"]
                pct = progress / total * 100

                t0 = time.time()
                try:
                    cached_start, cached_end = cached_ranges.get(issuer_name, ("", ""))
                    calc_start = forced_refresh.get(issuer_name, self.start_date)
                    if issuer_name not in forced_refresh and cached_start <= self.start_date and cached_end and cached_end < self.end_date:
                        calc_start = (datetime.strptime(cached_end, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")

                    result = calculate_issuer_deviations(
                        oracle_conn,
                        issuer_name,
                        calc_start,
                        self.end_date,
                        shared_cache=shared_cache,
                    )

                    # 保存结果（即使 calculated_bonds=0 也要记录，避免重复计算）
                    if issuer_name in forced_refresh:
                        self.cache_conn.execute(
                            """
                            DELETE FROM bond_deviations
                            WHERE issuer = ? AND issue_date_rule = ?
                              AND issue_date >= ? AND issue_date <= ?
                            """,
                            (issuer_name, ISSUE_DATE_RULE_VERSION, calc_start, self.end_date),
                        )

                    save_issuer_result(
                        self.cache_conn, result,
                        calc_start, self.end_date,
                    )

                    elapsed = time.time() - t0
                    bonds_info = f"{result['calculated_bonds']}/{result['total_bonds']}只"
                    non_mkt = result.get("non_market_count", 0)
                    status = f"⚠️{non_mkt}非市场化" if non_mkt > 0 else "✅"

                    print(f"   [{progress}/{total} {pct:.0f}%] {issuer_name}"
                          f" | {bonds_info} | {status} | {elapsed:.1f}s")

                    self._stats["completed"] += 1

                except Exception as e:
                    elapsed = time.time() - t0
                    print(f"   [{progress}/{total} {pct:.0f}%] {issuer_name}"
                          f" | ❌ 错误: {e} | {elapsed:.1f}s")
                    self._stats["errors"] += 1

                    # 即使出错也标记为"已处理"（写入空结果），避免重复卡死
                    try:
                        empty_result = {
                            "issuer": issuer_name,
                            "total_bonds": 0,
                            "calculated_bonds": 0,
                            "non_market_count": 0,
                            "non_market_ratio": 0.0,
                            "overpriced_count": 0,
                            "overpriced_ratio": 0.0,
                            "bonds": [],
                            "avg_deviation_bp": 0.0,
                        }
                        save_issuer_result(
                            self.cache_conn, empty_result,
                            self.start_date, self.end_date,
                        )
                    except Exception:
                        pass

            # 最后一个日期组的收尾
            if current_date_group and not _STOP_SIGNAL:
                print(f"   └─ 日期 {current_date_group} 完成 ({date_group_count} 个发行人)")

        # 更新 run_meta
        self._update_run_meta()
        self._print_summary()

    def _update_run_meta(self):
        """更新运行元数据"""
        self.cache_conn.execute("""
            INSERT OR REPLACE INTO run_meta (
                id, last_start_date, last_end_date, last_run_time,
                total_issuers, completed_issuers
            ) VALUES (1, ?, ?, ?, ?, ?)
        """, (
            self.start_date,
            self.end_date,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            self._stats["total"],
            self._stats["completed"],
        ))
        self.cache_conn.commit()

    def _print_summary(self):
        """打印汇总信息"""
        elapsed = time.time() - self._stats["start_time"]
        minutes = elapsed / 60

        print("\n" + "=" * 60)
        print("📊 构建完成汇总")
        print("=" * 60)
        print(f"   总发行人: {self._stats['total']}")
        print(f"   已完成:   {self._stats['completed']}")
        print(f"   本次跳过: {self._stats['skipped']}")
        print(f"   错误:     {self._stats['errors']}")
        print(f"   总耗时:   {minutes:.1f} 分钟")
        if self._stats["completed"] > self._stats["skipped"]:
            processed = self._stats["completed"] - self._stats["skipped"]
            avg = elapsed / processed if processed > 0 else 0
            print(f"   平均:     {avg:.1f}s / 发行人")
        print("=" * 60)

    def close(self):
        """关闭 SQLite 连接"""
        if self.cache_conn:
            self.cache_conn.close()


# ──────────────────────────────────────────────────────
# 后台持续运行模式
# ──────────────────────────────────────────────────────

def build_cache_once(
    start_date: str = HISTORY_START_DATE,
    end_date: str | None = None,
    progress=None,
) -> dict:
    """Run one incremental cache build for the portal background task."""
    target_end = end_date or datetime.now().strftime("%Y%m%d")
    if progress:
        progress("一级发行定价缓存：开始构建", 5)
    builder = DateOrderedCacheBuilder(start_date, target_end, CACHE_DB_PATH)
    try:
        builder.build()
        stats = dict(builder._stats)
        if progress:
            progress(
                f"一级发行定价缓存：完成 {stats['completed']}/{stats['total']} 家发行人",
                100,
            )
        return stats
    finally:
        builder.close()


def run_daemon(start_date: str, end_date: str, once: bool = False):
    """
    后台持续运行模式。

    完成一轮全量构建后，每30分钟检查一次是否有新数据需要更新。
    """
    global _STOP_SIGNAL

    # 注册信号处理
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    round_num = 0
    while not _STOP_SIGNAL:
        round_num += 1
        # 每轮动态更新 end_date 为今天
        # A one-shot backfill must honor --end; daemon mode follows today.
        current_end = end_date if once else datetime.now().strftime("%Y%m%d")

        print(f"\n{'━' * 60}")
        print(f"🔄 第 {round_num} 轮构建 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        print(f"{'━' * 60}")

        builder = DateOrderedCacheBuilder(start_date, current_end)
        try:
            builder.build()
        except Exception as e:
            print(f"\n❌ 构建过程异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            builder.close()

        if once or _STOP_SIGNAL:
            break

        # 等待下一轮
        wait_minutes = 30
        print(f"\n💤 等待 {wait_minutes} 分钟后进入下一轮...")
        print(f"   （按 Ctrl+C 安全退出）")

        for _ in range(wait_minutes * 60):
            if _STOP_SIGNAL:
                break
            time.sleep(1)

    print("\n✅ 缓存构建器已退出")


# ──────────────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="一级发行非市场化评估 - 缓存构建器（日期倒序策略）"
    )
    parser.add_argument(
        "--start", default=HISTORY_START_DATE,
        help=f"起始日期 (默认: {HISTORY_START_DATE})"
    )
    parser.add_argument(
        "--end", default=None,
        help="截止日期 (默认: 今天)"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="清除当前版本缓存重新构建"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="只跑一轮不循环"
    )

    args = parser.parse_args()
    end_date = args.end or datetime.now().strftime("%Y%m%d")

    if args.reset:
        print("🗑️  清除当前版本缓存...")
        conn = init_cache_db(CACHE_DB_PATH)
        conn.execute(
            "DELETE FROM bond_deviations WHERE issue_date_rule = ?",
            (ISSUE_DATE_RULE_VERSION,)
        )
        conn.execute(
            "DELETE FROM issuer_summary WHERE issue_date_rule = ?",
            (ISSUE_DATE_RULE_VERSION,)
        )
        conn.commit()
        conn.close()
        print("   ✅ 已清除")

    run_daemon(args.start, end_date, once=args.once)


if __name__ == "__main__":
    main()

