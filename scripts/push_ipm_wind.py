# -*- coding: utf-8 -*-
"""Wind 数据远程推送脚本（在本机/有 Wind 的机器上运行）。

背景：
  生产门户将部署在无 Wind 的服务器（如 http://10.6.8.78:5000/），行业景气
  高频数据（ipm_tracker，唯一依赖 Wind 的模块）改由本脚本在有 Wind 的机器上
  定时拉取并推送到服务器。

流程：
  1. GET  /api/ingest/ipm/preflight（令牌鉴权）→ 服务端数据最新日期
  2. 据此计算增量区间 [服务端最新日期+1, 今天]，连接 Wind 拉取全部指标
  3. 生成与 ipm_tracker/updater.py 同口径的增量文件（本机留档）
  4. POST /api/ingest/ipm 推送，服务端合并进缓存并归档，立即生效

配置（优先级：命令行参数 > 环境变量 > 默认值；环境变量可写入仓库 .env）：
  IPM_SERVER_URL     服务器地址，默认 http://10.6.8.78:5000
  IPM_INGEST_TOKEN   推送令牌，必须与服务端一致
  WIND_TERMINAL_EXE  可选，Wind 终端 exe 路径；设置后若终端未登录会自动拉起

用法：
  python scripts/push_ipm_wind.py                # 常规增量推送（定时任务用这个）
  python scripts/push_ipm_wind.py --dry-run      # 只拉取生成本机增量文件，不推送
  python scripts/push_ipm_wind.py --days 10      # 强制回看最近 10 天
  python scripts/push_ipm_wind.py --start 2026-08-01
  python scripts/push_ipm_wind.py --full         # 推送本机全量缓存（首次迁移/灾备恢复）
  python scripts/push_ipm_wind.py --merge-local  # 推送的同时合并进本机缓存
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

import requests  # noqa: E402

from ipm_tracker import routes as ipm_routes  # noqa: E402
from ipm_tracker import updater as ipm_updater  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ipm_push")

# 服务端无数据时的兜底回看天数（完整历史迁移请用 --full）
DEFAULT_LOOKBACK_DAYS = 7
PUSH_RETRIES = 3
PUSH_RETRY_INTERVAL = 10


def parse_args():
    parser = argparse.ArgumentParser(description="行业景气 Wind 数据远程推送")
    parser.add_argument('--server', help='服务器地址（默认 IPM_SERVER_URL 或 http://10.6.8.78:5000）')
    parser.add_argument('--token', help='推送令牌（默认 IPM_INGEST_TOKEN）')
    parser.add_argument('--full', action='store_true',
                        help='推送本机全量缓存 cache/indicators_data.json（整体替换服务端）')
    parser.add_argument('--start', help='手动指定增量起始日期 YYYY-MM-DD')
    parser.add_argument('--days', type=int, help='强制回看最近 N 天')
    parser.add_argument('--dry-run', action='store_true', help='只拉取生成本机增量文件，不推送')
    parser.add_argument('--merge-local', action='store_true',
                        help='推送成功后同时合并进本机主缓存（默认不动本机缓存）')
    parser.add_argument('--http-timeout', type=int, default=120, help='HTTP 超时秒数（默认120）')
    parser.add_argument('--max-retries', type=int, default=20, help='Wind 等待最大重试次数（默认20）')
    parser.add_argument('--interval', type=int, default=20, help='Wind 重试间隔秒数（默认20）')
    return parser.parse_args()


def maybe_launch_wind_terminal():
    """设置了 WIND_TERMINAL_EXE 时拉起 Wind 终端（无人值守定时任务用）"""
    exe = (os.environ.get("WIND_TERMINAL_EXE") or "").strip()
    if not exe or not os.path.isfile(exe):
        return
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None and any(
        (p.name() or '').lower().startswith('wind') for p in psutil.process_iter(['name'])
    ):
        return
    log.info(f"拉起 Wind 终端: {exe}")
    subprocess.Popen([exe], cwd=os.path.dirname(exe),
                     creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)


def server_request(method, url, token, timeout, **kwargs):
    headers = kwargs.pop('headers', {})
    headers['X-Ingest-Token'] = token
    resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if resp.status_code in (401, 503):
        try:
            message = resp.json().get('message', resp.text)
        except ValueError:
            message = resp.text
        raise RuntimeError(f"服务端拒绝（HTTP {resp.status_code}）：{message}")
    resp.raise_for_status()
    return resp.json()


def preflight(server, token, timeout):
    data = server_request('GET', f"{server}/api/ingest/ipm/preflight", token, timeout)
    log.info("服务端就绪：数据最新日期=%s 缓存更新于=%s 指标数=%s",
             data.get('data_latest_date'), data.get('cache_updated'), data.get('indicators'))
    return data


def push_payload(server, token, timeout, payload):
    last_error = None
    for attempt in range(1, PUSH_RETRIES + 1):
        try:
            data = server_request('POST', f"{server}/api/ingest/ipm", token, timeout,
                                  json=payload)
            log.info("推送成功：模式=%s 归档日期=%s 推送指标=%s 服务端最新日期=%s",
                     data.get('mode'), data.get('increment_date'),
                     data.get('indicators'), data.get('data_latest_date'))
            return data
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            log.warning("推送失败（第 %d/%d 次）：%s", attempt, PUSH_RETRIES, exc)
            if attempt < PUSH_RETRIES:
                time.sleep(PUSH_RETRY_INTERVAL)
    raise RuntimeError(f"推送重试 {PUSH_RETRIES} 次均失败：{last_error}")


def save_increment_file(payload):
    """增量文件按 updater 口径落本机 data\\ 目录留档（也是本机增量锚点）"""
    data_dir = Path(ipm_routes.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / f"{payload['date']}.json"
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, target)
    log.info("本机增量文件已保存: %s", target)


def run_full_push(args, server, token):
    """全量推送：把本机主缓存整体推给服务端替换"""
    cache_file = Path(ipm_routes.CACHE_FILE)
    if not cache_file.is_file():
        raise RuntimeError(f"本机全量缓存不存在：{cache_file}")
    with open(cache_file, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    indicators = payload.get('indicators') or {}
    if not indicators:
        raise RuntimeError("本机全量缓存为空，取消推送")
    log.info("全量推送 %d 个指标（来源 %s）...", len(indicators), cache_file)
    if args.dry_run:
        log.info("--dry-run：跳过推送")
        return
    push_payload(server, token, args.http_timeout, {'indicators': indicators})


def run_increment_push(args, server, token):
    """常规增量推送：服务端最新日期 → 今天"""
    status = preflight(server, token, args.http_timeout)
    latest = status.get('data_latest_date')
    today = date.today()
    end_date = today.strftime('%Y-%m-%d')

    if args.start:
        start_date = args.start
        log.info("起始日期：手动指定 %s", start_date)
    elif args.days:
        start_date = (today - timedelta(days=args.days)).strftime('%Y-%m-%d')
        log.info("起始日期：回看 %d 天 → %s", args.days, start_date)
    elif latest:
        latest_day = datetime.strptime(latest, '%Y-%m-%d').date()
        if latest_day >= today:
            log.info("服务端数据已覆盖今天（%s），无需推送", latest)
            return
        start_date = (latest_day + timedelta(days=1)).strftime('%Y-%m-%d')
        log.info("起始日期：服务端最新 %s + 1 → %s", latest, start_date)
    else:
        start_date = (today - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
        log.info("服务端暂无数据，兜底回看 %d 天 → %s（完整历史请用 --full）",
                 DEFAULT_LOOKBACK_DAYS, start_date)

    maybe_launch_wind_terminal()
    wind = ipm_updater.connect_wind(args.max_retries, args.interval)
    sids = ipm_updater.load_indicator_ids()
    log.info("拉取区间 [%s, %s]，共 %d 个指标...", start_date, end_date, len(sids))
    series, failed = ipm_updater.fetch_incremental(wind, sids, start_date, end_date)

    if not series and failed:
        sample = '；'.join(f"{item['id']}: {item['error']}" for item in failed[:3])
        raise RuntimeError(f"Wind 未返回可用指标数据（失败 {len(failed)} 个）：{sample}")
    if not series:
        log.info("区间内无新数据，结束")
        return

    payload = {
        'date': today.strftime('%Y%m%d'),
        'start': start_date,
        'end': end_date,
        'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'updated_indicators': len(series),
        'failed': failed,
        'indicators': series,
    }
    save_increment_file(payload)
    if args.dry_run:
        log.info("--dry-run：跳过推送（如需推送请去掉该参数重跑，区间内数据不会被重复拉取影响）")
        return
    push_payload(server, token, args.http_timeout, payload)
    if args.merge_local:
        merged = ipm_updater.merge_to_cache(series)
        log.info("本机主缓存已同步合并 %d 个指标", merged)


def main():
    args = parse_args()
    log.info("=" * 60)
    log.info("Wind 数据远程推送开始")

    server = (args.server or os.environ.get("IPM_SERVER_URL") or "http://10.6.8.78:5000").rstrip("/")
    token = (args.token or os.environ.get("IPM_INGEST_TOKEN") or "").strip()
    if not token:
        log.error("缺少推送令牌：请在 .env 或环境变量中配置 IPM_INGEST_TOKEN（与服务端一致）")
        return 1
    log.info("目标服务器：%s", server)

    try:
        if args.full:
            run_full_push(args, server, token)
        else:
            run_increment_push(args, server, token)
    except Exception as exc:
        log.error("推送失败：%s", exc)
        log.error("提示：Wind 未登录/服务器未启动/令牌不一致均会导致失败，详见部署文档 deploy/wind_push_setup.md")
        return 1
    log.info("推送流程结束 ✅")
    return 0


if __name__ == '__main__':
    sys.exit(main())
