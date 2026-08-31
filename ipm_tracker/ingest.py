# -*- coding: utf-8 -*-
"""行业景气高频数据 · 远程推送接收接口（供无 Wind 部署点接收数据）。

数据流（有 Wind 机器 → 无 Wind 服务器）：
  - 有 Wind 的机器运行 scripts/push_ipm_wind.py（定时任务），连 Wind 拉取行业
    景气指标增量，POST 到本接口；
  - 本接口校验令牌与数据结构后，复用 routes 的合并/归档逻辑写入统一缓存，
    运行中的服务立即生效，无需重启，也无需 Wind 在线。

安全模型：
  - 端点不走门户站点登录（session），改用独立令牌：请求头 X-Ingest-Token
    必须等于服务端环境变量 IPM_INGEST_TOKEN；未配置令牌时接口一律 503 停用。
  - 本蓝图刻意不加入 app.py 的登录保护蓝图清单，鉴权完全由令牌承担。
"""
import hmac
import json
import logging
import os
from datetime import datetime

from flask import Blueprint, jsonify, request

from ipm_tracker import routes as ipm_routes

log = logging.getLogger("ipm_ingest")

bp = Blueprint("ipm_ingest", __name__)

# 单次推送体积上限（全量缓存约几 MB，留足余量）
MAX_BODY_BYTES = 64 * 1024 * 1024


def _ingest_token():
    return (os.environ.get("IPM_INGEST_TOKEN") or "").strip()


def _token_ok(submitted):
    expected = _ingest_token()
    if not expected:
        return False
    return hmac.compare_digest(str(submitted), expected)


def _request_token():
    return request.headers.get("X-Ingest-Token") or ""


def _cache_meta():
    """读取磁盘缓存元信息（不依赖内存态），返回 (meta_dict, indicators_dict)"""
    meta = ipm_routes.load_cache_file()
    return meta, dict(ipm_routes.series_cache or {})


def _data_latest_date(indicators):
    latest = None
    for rec in indicators.values():
        times = rec.get("times") or []
        if times:
            latest = max(latest or "", times[-1])
    return latest


def _increment_files():
    names = []
    if os.path.isdir(ipm_routes.DATA_DIR):
        for name in os.listdir(ipm_routes.DATA_DIR):
            stem = os.path.splitext(name)[0]
            if len(stem) == 8 and stem.isdigit():
                names.append(stem)
    return sorted(names)


def _validate_indicators(inc):
    """校验 indicators 结构：{sid: {times: [..], values: [..]}}；返回错误信息或 None"""
    if not isinstance(inc, dict) or not inc:
        return "缺少 indicators 数据"
    for sid, rec in list(inc.items())[:5]:
        if not isinstance(rec, dict) or not isinstance(rec.get("times"), list) \
                or not isinstance(rec.get("values"), list):
            return f"指标 {sid} 结构异常（需要 times/values 数组）"
    return None


@bp.route("/api/ingest/ipm/preflight", methods=["GET"])
def ingest_preflight():
    """推送前查询：服务端数据覆盖到哪天（推送脚本据此计算增量区间）。"""
    if not _ingest_token():
        return jsonify({'status': 'error',
                        'message': '服务端未配置 IPM_INGEST_TOKEN，推送通道未启用'}), 503
    if not _token_ok(_request_token()):
        return jsonify({'status': 'error', 'message': '令牌无效'}), 401

    meta, indicators = _cache_meta()
    return jsonify({
        'status': 'ok',
        'data_latest_date': _data_latest_date(indicators),
        'cache_updated': (meta or {}).get('updated'),
        'indicators': len(indicators),
        'last_increment': _increment_files()[-1] if _increment_files() else None,
        'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@bp.route("/api/ingest/ipm", methods=["POST"])
def ingest_push():
    """接收推送的数据文件（增量或全量，自动识别，同旧网页上传口径）。

    请求：JSON body
      - 增量：{"date": "YYYYMMDD", "start": "...", "end": "...", "indicators": {...}}
      - 全量：{"indicators": {...}}（整体替换缓存，用于首次迁移/灾备恢复）
    响应：合并/替换结果与服务端最新数据日期。
    """
    if not _ingest_token():
        return jsonify({'status': 'error',
                        'message': '服务端未配置 IPM_INGEST_TOKEN，推送通道未启用'}), 503
    if not _token_ok(_request_token()):
        return jsonify({'status': 'error', 'message': '令牌无效'}), 401

    length = request.content_length or 0
    if length > MAX_BODY_BYTES:
        return jsonify({'status': 'error', 'message': '请求体过大'}), 413

    payload = request.get_json(silent=True, force=True)
    if not isinstance(payload, dict):
        return jsonify({'status': 'error', 'message': '未收到有效的 JSON 数据'}), 400

    inc = payload.get('indicators')
    error = _validate_indicators(inc)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    is_increment = isinstance(payload.get('date'), str) and 'start' in payload and 'end' in payload
    inc_date = None

    with ipm_routes._cache_lock:
        if is_increment:
            merged = ipm_routes.merge_increment_into_cache(inc)
            date_str = payload['date']
            if not (len(date_str) == 8 and date_str.isdigit()):
                date_str = datetime.now().strftime('%Y%m%d')
                payload['date'] = date_str
            inc_date = date_str
            os.makedirs(ipm_routes.DATA_DIR, exist_ok=True)
            archive = os.path.join(ipm_routes.DATA_DIR, f'{date_str}.json')
            with open(archive, 'w', encoding='utf-8') as fp:
                json.dump(payload, fp, ensure_ascii=False)
            mode_desc = f'增量合并 {date_str}（{merged} 个指标）'
        else:
            ipm_routes.series_cache = inc
            mode_desc = '全量替换'
        ipm_routes.save_cache_file()
        ipm_routes.load_cache_file()

    _, indicators = _cache_meta()
    log.info("Ingest from %s: %s, payload holds %d indicators", request.remote_addr,
             mode_desc, len(inc))
    return jsonify({
        'status': 'ok',
        'mode': 'increment' if is_increment else 'full',
        'increment_date': inc_date,
        'indicators': len(inc),
        'data_latest_date': _data_latest_date(indicators),
        'cache_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
