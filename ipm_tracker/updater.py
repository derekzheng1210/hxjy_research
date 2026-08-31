# -*- coding: utf-8 -*-
"""
每日增量更新脚本（开机自动 / 定时任务 / 手动均可）

职责：
  1. 扫描 data\\ 目录，找到最近一次增量文件的日期（如 20260811）
  2. 计算增量区间 = [最近增量日期 + 1天, 今天]
  3. 连接 Wind（自动重试等待，直至 Wind 终端登录就绪）
  4. 拉取全部指标在该区间内的数据（只保留有新值的指标）
  5. 保存为 data\\YYYYMMDD.json（当日日期命名，作为"一个上传文件"）
  6. 【默认】不动本机缓存，只生成上传文件，供网页『⟳ 更新数据』上传；
     加 --merge 才额外合并进本机 cache\\indicators_data.json
  7. 追加日志到 data\\update_log.txt

用法：
  python update_daily.py                 # 自动增量，只生成上传文件（默认，不动本机缓存）
  python update_daily.py --merge         # 额外合并进本机主缓存 cache\\indicators_data.json
  python update_daily.py --force         # 忽略上次增量日期，强制重拉最近 N 天
  python update_daily.py --start 2026-08-01   # 手动指定起始日期
  python update_daily.py --max-retries 30 --interval 20   # 调整 Wind 等待参数
"""
import os
import sys
import json
import time
import glob
import argparse
import logging
import site
from datetime import datetime, timedelta
from pathlib import Path

from .routes import (
    CACHE_DIR,
    CACHE_FILE,
    DATA_DIR,
    EXCEL_PATH,
    ensure_runtime_data,
    refresh_cache,
)

LOG_FILE = os.path.join(DATA_DIR, "update_log.txt")

# 与 app.py 保持一致：被移除的指标不拉取
EXCLUDED_INDICATORS = {'G0303266'}
# 首次运行（无缓存无增量）时的兜底全量起始日期
FULL_START_DATE = "2021-01-01"
# 强制模式兜底回看天数
FORCE_LOOKBACK_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ipm_update")


# ── 指标表读取 ──────────────────────────────────────────
def load_indicator_ids():
    """从 指标对应表.xlsx 读取全部指标 ID（与 app.py 同口径）"""
    import pandas as pd
    if not os.path.isfile(EXCEL_PATH):
        log.error(f"指标对应表不存在: {EXCEL_PATH}")
        sys.exit(1)
    sids = []
    xls = pd.ExcelFile(EXCEL_PATH)
    for sheet in xls.sheet_names:
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet, dtype=str)
        for _, row in df.iterrows():
            sid = str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else ''
            if sid and sid != 'nan' and sid not in EXCLUDED_INDICATORS and sid not in sids:
                sids.append(sid)
    log.info(f"指标表加载完成: {len(sids)} 个指标")
    return sids


# ── 日期逻辑 ────────────────────────────────────────────
def find_last_increment_date():
    """扫描 data\\ 目录，返回最近一次增量文件日期（date 对象）；无则 None"""
    if not os.path.isdir(DATA_DIR):
        return None
    last = None
    for name in os.listdir(DATA_DIR):
        stem = os.path.splitext(name)[0]
        if len(stem) == 8 and stem.isdigit():
            try:
                d = datetime.strptime(stem, '%Y%m%d').date()
            except ValueError:
                continue
            if last is None or d > last:
                last = d
    return last


def cache_last_data_date():
    """读主缓存中所有指标的最后数据日期，取最大值；无缓存返回 None"""
    if not os.path.isfile(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        last = None
        for rec in (payload.get('indicators') or {}).values():
            times = rec.get('times') or []
            if not times:
                continue
            try:
                d = datetime.strptime(times[-1], '%Y-%m-%d').date()
            except ValueError:
                continue
            if last is None or d > last:
                last = d
        return last
    except Exception as e:
        log.warning(f"读取缓存最后日期失败: {e}")
        return None


# ── Wind 连接（等待登录） ───────────────────────────────
def connect_wind(max_retries, interval):
    """启动 WindPy 并等待终端就绪；返回 wind 对象"""
    sdk_dir = Path(os.environ.get(
        "WINDPY_HOME",
        r"C:\Wind\Wind.NET.Client\WindNET\x64",
    ))
    if str(sdk_dir) not in sys.path:
        sys.path.insert(0, str(sdk_dir))
    # Wind 官方 WindPy.py 会读取 site-packages/WindPy.pth 来定位 WindPy.dll，
    # 且旧版实现不会去除换行，因此必须按无尾部换行的格式写入。
    runtime_site = Path(DATA_DIR).parent / "site-packages"
    runtime_site.mkdir(parents=True, exist_ok=True)
    runtime_pth = runtime_site / "WindPy.pth"
    runtime_pth.write_text(str(sdk_dir), encoding="ascii")
    if str(runtime_site) not in sys.path:
        sys.path.insert(0, str(runtime_site))
    for site_dir in site.getsitepackages():
        if Path(site_dir).name.lower() != "site-packages":
            continue
        pth_file = Path(site_dir) / "WindPy.pth"
        expected = str(sdk_dir)
        try:
            if not pth_file.exists() or pth_file.read_text(encoding="ascii") != expected:
                pth_file.write_text(expected, encoding="ascii")
        except OSError:
            continue
    from WindPy import w
    for i in range(1, max_retries + 1):
        try:
            w.start()
            if w.isconnected():
                log.info(f"Wind 连接成功（第 {i} 次尝试）")
                return w
            log.warning(f"Wind 已启动但未登录（第 {i}/{max_retries} 次）")
        except Exception as e:
            log.warning(f"Wind 连接异常（第 {i}/{max_retries} 次）: {e}")
        if i < max_retries:
            log.info(f"  {interval} 秒后重试，请确认 Wind 终端已打开并完成登录...")
            time.sleep(interval)
    raise RuntimeError("Wind 连接超时：请确认 Wind 金融终端已启动并登录")


# ── 增量拉取 ────────────────────────────────────────────
def fetch_incremental(w, sids, start_date, end_date, progress=None):
    """拉取 [start, end] 区间全部指标数据；返回 (有数据的指标, 失败列表)"""
    series, failed = {}, []
    for i, sid in enumerate(sids):
        try:
            data = w.wsd(sid, "close", start_date, end_date, "")
        except Exception as e:
            failed.append({'id': sid, 'error': str(e)})
            continue
        if data.ErrorCode == 0 and data.Data and len(data.Data[0]) > 0:
            vals = [round(float(v), 4) if v is not None else None for v in data.Data[0]]
            times = [t.strftime('%Y-%m-%d') for t in data.Times]
            if any(v is not None for v in vals):
                series[sid] = {'times': times, 'values': vals}
        else:
            failed.append({'id': sid, 'error': f"ErrorCode={data.ErrorCode}"})
        if (i + 1) % 20 == 0 or i + 1 == len(sids):
            message = f"行业景气指标 {i + 1}/{len(sids)}（成功 {len(series)}，失败 {len(failed)}）"
            log.info(message)
            if progress:
                progress(message)
    return series, failed


# ── 合并到主缓存 ────────────────────────────────────────
def merge_to_cache(incremental):
    """把增量数据合并进 cache\\indicators_data.json（按日期去重排序）"""
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {'indicators': {}}
    if os.path.isfile(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)

    indicators = payload.get('indicators', {})
    merged_count = 0
    for sid, rec in incremental.items():
        if sid not in indicators:
            indicators[sid] = {'times': [], 'values': []}
        # dict 按日期去重：增量覆盖同日期旧值
        combo = dict(zip(indicators[sid].get('times', []), indicators[sid].get('values', [])))
        for t, v in zip(rec['times'], rec['values']):
            if v is None:
                # 增量里 Wind 返回的空值不覆盖已有历史值，避免数据被清空
                continue
            combo[t] = v
        items = sorted(combo.items())  # ISO 日期字符串排序 = 时间序
        indicators[sid] = {'times': [t for t, _ in items], 'values': [v for _, v in items]}
        merged_count += 1

    payload['indicators'] = indicators
    payload['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    payload['start_date'] = payload.get('start_date', FULL_START_DATE)
    payload['last_increment'] = datetime.now().strftime('%Y-%m-%d')

    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    log.info(f"主缓存已合并更新: {merged_count} 个指标, {len(indicators)} 个指标总览, 文件 {CACHE_FILE}")
    return merged_count


# ── 日志 ────────────────────────────────────────────────
def append_log(line):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning(f"写日志失败: {e}")


def run_update(progress=None, force=False):
    """由门户后台调用 Wind 增量更新，并直接合并进统一缓存。"""
    report = progress or (lambda _message, _percent=None: None)
    ensure_runtime_data()
    today = datetime.now().date()
    end_date = today.strftime('%Y-%m-%d')
    last_inc = find_last_increment_date()

    if force:
        start_date = (today - timedelta(days=FORCE_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    elif last_inc is not None:
        if last_inc >= today:
            refresh_cache(force=True)
            message = f"行业景气高频数据今天（{today:%Y-%m-%d}）已更新，无需重复拉取"
            report(message)
            return {'ok': True, 'skipped': True, 'message': message}
        start_date = (last_inc + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        last_data = cache_last_data_date()
        start_date = (
            (last_data + timedelta(days=1)).strftime('%Y-%m-%d')
            if last_data else FULL_START_DATE
        )

    if start_date > end_date:
        refresh_cache(force=True)
        message = f"行业景气高频数据已覆盖至 {end_date}"
        report(message)
        return {'ok': True, 'skipped': True, 'message': message}

    report(f"连接 Wind，准备更新行业景气高频数据 {start_date} 至 {end_date}")
    wind = connect_wind(max_retries=1, interval=0)
    sids = load_indicator_ids()
    series, failed = fetch_incremental(wind, sids, start_date, end_date, progress=report)
    if not series and failed:
        sample = '；'.join(f"{item['id']}: {item['error']}" for item in failed[:3])
        raise RuntimeError(f"Wind 未返回可用指标数据（失败 {len(failed)} 个）：{sample}")

    os.makedirs(DATA_DIR, exist_ok=True)
    today_name = today.strftime('%Y%m%d')
    inc_file = os.path.join(DATA_DIR, f"{today_name}.json")
    payload = {
        'date': today_name,
        'start': start_date,
        'end': end_date,
        'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'updated_indicators': len(series),
        'failed': failed,
        'indicators': series,
    }
    tmp_file = inc_file + '.tmp'
    with open(tmp_file, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(tmp_file, inc_file)

    merged = merge_to_cache(series)
    refresh_cache(force=True)
    status = f"行业景气高频数据更新完成：{merged} 个指标，区间 {start_date} 至 {end_date}"
    if failed:
        status += f"，{len(failed)} 个指标未返回数据"
    append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S} {status}")
    report(status)
    return {
        'ok': True,
        'updated_indicators': merged,
        'failed': len(failed),
        'start': start_date,
        'end': end_date,
        'increment_file': inc_file,
    }


# ── 主流程 ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="IPM 每日增量数据更新")
    parser.add_argument('--force', action='store_true', help='忽略上次增量日期强制重拉')
    parser.add_argument('--start', help='手动指定起始日期 YYYY-MM-DD')
    # 默认只生成上传文件、不动本机缓存；--merge 才合并主缓存
    parser.add_argument('--merge', action='store_true',
                        help='额外合并进本机主缓存 cache\\indicators_data.json'
                             '（默认只生成增量文件，不动本机缓存）')
    parser.add_argument('--no-merge', dest='merge', action='store_false',
                        help='[已废弃，现为默认行为] 只生成增量文件，不合并主缓存')
    parser.add_argument('--max-retries', type=int, default=20, help='Wind 等待最大重试次数（默认20）')
    parser.add_argument('--interval', type=int, default=20, help='Wind 重试间隔秒数（默认20）')
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("IPM 每日增量更新开始")

    today = datetime.now().date()
    end_date = today.strftime('%Y-%m-%d')

    # 1) 确定起始日期
    last_inc = find_last_increment_date()
    if args.start:
        start_date = args.start
        log.info(f"[起始] 手动指定: {start_date}")
    elif args.force:
        start_date = (today - timedelta(days=FORCE_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
        log.info(f"[起始] 强制模式: 回看 {FORCE_LOOKBACK_DAYS} 天 → {start_date}")
    elif last_inc is not None:
        if last_inc >= today:
            log.info(f"今天（{today}）已更新过（data\\{today:%Y%m%d}.json 存在），无需重复更新。")
            log.info("如需强制重拉请加 --force。")
            append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S} 跳过：今天已更新")
            return 0
        start_date = (last_inc + timedelta(days=1)).strftime('%Y-%m-%d')
        log.info(f"[起始] 上次增量 {last_inc} → 本次从 {start_date} 开始")
    else:
        # 无增量文件：优先从主缓存最后数据日期继续；无缓存则全量
        last_data = cache_last_data_date()
        if last_data:
            start_date = (last_data + timedelta(days=1)).strftime('%Y-%m-%d')
            log.info(f"[起始] 无增量文件，从主缓存最后日期 {last_data} 继续 → {start_date}")
        else:
            start_date = FULL_START_DATE
            log.info(f"[起始] 无缓存无增量，全量拉取 {start_date} → {end_date}")

    if start_date > end_date:
        # 缓存/上次增量已覆盖到今天：若今天尚无增量文件，先创建锚点文件，
        # 作为后续增量更新的基准日期（避免每次重复判定为"无需更新"）。
        os.makedirs(DATA_DIR, exist_ok=True)
        today_name = today.strftime('%Y%m%d')
        anchor_file = os.path.join(DATA_DIR, f"{today_name}.json")
        if not os.path.isfile(anchor_file):
            with open(anchor_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'date': today_name,
                    'start': start_date,
                    'end': end_date,
                    'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'note': '主缓存已含当日数据，本次无增量；此文件作为增量基准锚点',
                    'updated_indicators': 0,
                    'failed': [],
                    'indicators': {},
                }, f, ensure_ascii=False)
            log.info(f"今日数据已是最新，已创建增量基准锚点: {anchor_file}")
            append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S} 无需更新（数据已含当日），建立锚点 data\\{today_name}.json")
        else:
            log.info(f"起始日期 {start_date} 晚于今天 {end_date}，数据已最新，无需更新。")
            log.info("如需重新生成今天的上传文件，请运行 python update_daily.py --force --no-merge")
        return 0

    # 2) 连接 Wind（重试等待登录）
    log.info("正在连接 Wind（若未打开终端将自动等待，最多 "
             f"{args.max_retries * args.interval} 秒）...")
    try:
        w = connect_wind(args.max_retries, args.interval)
    except RuntimeError as e:
        log.error(str(e))
        append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S} 失败：{e}")
        return 1

    # 3) 拉取增量
    sids = load_indicator_ids()
    log.info(f"拉取增量区间 [{start_date}, {end_date}]，共 {len(sids)} 个指标...")
    series, failed = fetch_incremental(w, sids, start_date, end_date)
    log.info(f"增量拉取完成：{len(series)} 个指标有新数据，{len(failed)} 个失败")

    # 4) 保存当日增量文件
    os.makedirs(DATA_DIR, exist_ok=True)
    today_name = today.strftime('%Y%m%d')
    inc_file = os.path.join(DATA_DIR, f"{today_name}.json")
    payload = {
        'date': today_name,
        'start': start_date,
        'end': end_date,
        'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'updated_indicators': len(series),
        'failed': failed,
        'indicators': series,
    }
    with open(inc_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    log.info(f"增量文件已保存: {inc_file}（{len(series)} 个指标）")
    log.info("※ 这就是要上传的“一个文件”：")
    log.info("  请打开服务器网页，点顶栏『⟳ 更新数据』按钮（需口令），")
    log.info(f"  选择该文件（data\\{today_name}.json）上传，网页将自动呈现最新数据。")

    # 5) 合并主缓存（默认不合并；仅显式 --merge 才合并本机缓存）
    if args.merge:
        merge_to_cache(series)
    else:
        log.info("（默认模式）本机主缓存未改动，本文件即为要上传的更新文件。")

    # 6) 日志
    status = f"成功（{len(series)} 指标，区间 {start_date}~{end_date}）" if len(series) else "成功（区间内无新数据）"
    if failed:
        status += f"，失败 {len(failed)} 个"
    append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S} {status} → data\\{today_name}.json")
    log.info("更新流程结束 ✅")
    return 0


if __name__ == '__main__':
    sys.exit(main())
