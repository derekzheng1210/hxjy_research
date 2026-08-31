"""
IPM 行业景气度数据面板 - Flask 后端（纯网站服务）

数据流（2026-08-29 起）：
  - 网站服务【全程不连接 Wind】，所有数据一律读本地缓存 cache\\indicators_data.json
  - 只有更新数据时才需要 Wind：运行 update_daily.py（手动 / wind_update_now.bat /
    wind_auto_update.bat 定时任务），该脚本连 Wind 拉取增量并合并进本地缓存
  - 更新完成后，运行中的服务调用 POST /api/data/refresh 即可重新读盘加载新数据，
    无需重启，也无需 Wind 在线
"""
import logging
import sys
import os
import json
import shutil
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify, request, render_template, send_file, redirect, url_for

from paths import DATA_DIR as PORTAL_DATA_DIR

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("ipm")

# ── Wind API ──────────────────────────────────────────────
# 网站服务不连接 Wind（本文件刻意不 import WindPy）。
# 仅 update_daily.py 在更新数据时连接 Wind，请使用：
#   python update_daily.py   或   wind_update_now.bat（手动）/ wind_auto_update.bat（定时）

# ── Configuration ─────────────────────────────────────────
# Excel 路径优先级：环境变量 > 当前目录下的 指标对应表.xlsx > 默认路径
PACKAGE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PORTAL_DATA_DIR / "ipm_tracker"
_EXCEL_PATH_ENV = os.environ.get("IPM_EXCEL_PATH", "").strip()
_EXCEL_PATH_LOCAL = str(PACKAGE_DIR / "指标对应表.xlsx")
if _EXCEL_PATH_ENV and os.path.isfile(_EXCEL_PATH_ENV):
    EXCEL_PATH = _EXCEL_PATH_ENV
elif os.path.isfile(_EXCEL_PATH_LOCAL):
    EXCEL_PATH = _EXCEL_PATH_LOCAL
else:
    EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "指标对应表.xlsx")

HOST = os.environ.get("IPM_HOST", "0.0.0.0")
PORT = int(os.environ.get("IPM_PORT", "5000"))

# ── Local Cache Configuration ─────────────────────────────
# 网站唯一数据来源：本地缓存文件（由 update_daily.py 连 Wind 更新）。
# 网页接口一律读缓存，服务端本身绝不调用 Wind API。
CACHE_DIR = str(RUNTIME_DIR / "cache")
CACHE_FILE = str(RUNTIME_DIR / "cache" / "indicators_data.json")
# 每日增量数据目录：update_daily.py 每次更新保存 data\\YYYYMMDD.json
DATA_DIR = str(RUNTIME_DIR / "data")
# 全量缓存起始日期（写缓存文件时作为元信息保留）
CACHE_START_DATE = "2021-01-01"

# Indicators explicitly removed from the dashboard
EXCLUDED_INDICATORS = {'G0303266'}  # 韩国半导体设备进口同比 (data discontinued)

bp = Blueprint(
    "ipm_tracker",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)


def ensure_runtime_data():
    """首次并入门户时，把随模块提供的基准数据复制到统一运行数据目录。"""
    cache_dir = Path(CACHE_DIR)
    data_dir = Path(DATA_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    seed_cache = PACKAGE_DIR / "seed" / "cache" / "indicators_data.json"
    cache_path = Path(CACHE_FILE)
    if not cache_path.exists() and seed_cache.exists():
        shutil.copy2(seed_cache, cache_path)
    seed_data = PACKAGE_DIR / "seed" / "data"
    if seed_data.exists():
        for source in seed_data.glob("*.json"):
            target = data_dir / source.name
            if not target.exists():
                shutil.copy2(source, target)

# ── Unit Mapping ──────────────────────────────────────────
# Based on indicator name patterns and verified data magnitudes from Wind API
UNIT_MAP = {
    # === 消费 - 农林牧渔 ===
    'S5914495': '元/公斤', 'S0273733': '元/吨', 'S5021738': '元/头', 'S5021739': '元/头',
    'S5063761': '元/公斤', 'S5914496': '元/吨',
    # === 消费 - 食品饮料 ===
    'S0028099': '万吨', 'S0028083': '万吨', 'S5010222': '元/公斤', 'S5010223': '元/公斤',
    # === 消费 - 家用电器 ===
    'S0028203': '%', 'M7071735': '%', 'S0072018': '台', 'S0072043': '台', 'S0028211': '%',
    # === 消费 - 休闲服务 ===
    'M5529689': '万美元', 'Z0164070': '架次', 'S6623763': '万人次', 'S6623764': '万元',
    'M0061660': '%', 'S6710167': '万人次',
    # === 消费 - 美妆 ===
    'M0045719': '亿元',
    # === 金融地产 ===
    'S2707133': '%', 'S2707411': '%', 'S2707425': '%', 'S0073300': '%', 'S0029657': '%',
    'S0073290': '%', 'S0073293': '%', 'S0073297': '%', 'M0058005': '%', 'S0000021': '%',
    'S0073244': '%', 'S0073245': '%', 'S0073254': '%',
    # === 金融地产 - 利率 ===
    'M1001795': '%', 'M1006337': '%', 'M1001855': '%', 'M1001940': '%',
    # === 金融地产 - 市场 ===
    'M0331263': '%', 'M0330255': '点',
    # === 科技 ===
    'S5600228': '%', 'S5616792': '%', 'S0179733': '点', 'S5616803': '%',
    'S0028238': '%', 'S0028183': '%', 'S0072678': '万美元', 'Y6942301': '亿元',
    'S0070287': '亿只', 'A8879508': '亿新台币', 'S6500002': '%', 'S9901348': '万元',
    # === 制造 ===
    'S0243297': '台', 'S6018761': '台', 'S6006711': '点', 'S6018631': '万修正总吨',
    'S5914286': '点', 'Z8963351': '兆瓦时', 'S0105526': '辆', 'S0105711': '辆',
    'S5125927': '点', 'S5151661': '万千瓦', 'S0000298': '点',
    # === 周期 ===
    'O2147044': '元/吨', 'S5104570': '元/吨', 'S0026992': '%', 'M0330377': '元/吨',
    'J5336989': '点', 'S0029751': '美元/吨', 'S0181382': '元/吨', 'S5914515': '点',
    'S0027702': '万吨', 'S0031525': '美元/桶',
    # === 公用 ===
    'S0031550': '点', 'S6404620': '点', 'S0114089': '点', 'S0265138': '元/件',
    'S0000149': '万件', 'S0044633': '万人', 'S0036005': '万人', 'S0036025': '%',
}

# ── Data loading ──────────────────────────────────────────
indicators = {}       # id -> {一级, 二级, 指标名称, 单位}
categories = OrderedDict()  # 一级 -> {二级: [indicator_ids]}
series_cache = None   # 本地文件缓存 {sid: {times, values}}（2021-01-01 起全量）

# ── Local Cache Helpers ───────────────────────────────────

def load_cache_file():
    """从本地文件加载缓存到内存；返回缓存元信息（无缓存返回 None）"""
    global series_cache
    if not os.path.isfile(CACHE_FILE):
        series_cache = None
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        series_cache = payload.get('indicators', {})
        log.info(f"Cache loaded from {CACHE_FILE} (updated {payload.get('updated')}, {len(series_cache)} indicators)")
        return payload
    except Exception as e:
        log.warning(f"Failed to load cache file: {e}")
        series_cache = None
        return None


_cache_lock = threading.Lock()  # 上传合并/替换缓存时互斥，避免并发写坏文件


def save_cache_file():
    """将内存缓存原子写回本地文件（先写临时文件再替换，避免写一半被读到）"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'start_date': CACHE_START_DATE,
        'last_increment': datetime.now().strftime('%Y-%m-%d'),
        'indicators': series_cache or {},
    }
    tmp_file = CACHE_FILE + '.tmp'
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp_file, CACHE_FILE)
    log.info(f"Cache saved to {CACHE_FILE} ({len(series_cache or {})} indicators)")


def merge_increment_into_cache(incremental):
    """把增量数据 {sid:{times,values}} 合并进内存缓存（与 update_daily.py 同口径：
    同日期以新值覆盖旧值；增量中的空值不覆盖已有历史值）。返回合并的指标数。"""
    global series_cache
    if series_cache is None:
        series_cache = {}
    merged = 0
    for sid, rec in incremental.items():
        times = (rec or {}).get('times') or []
        values = (rec or {}).get('values') or []
        if sid not in series_cache:
            series_cache[sid] = {'times': [], 'values': []}
        # dict 按日期去重：增量覆盖同日期旧值
        combo = dict(zip(series_cache[sid].get('times', []),
                         series_cache[sid].get('values', [])))
        for t, v in zip(times, values):
            if v is None:
                continue
            combo[t] = v
        items = sorted(combo.items())  # ISO 日期字符串排序 = 时间序
        series_cache[sid] = {'times': [t for t, _ in items],
                             'values': [v for _, v in items]}
        merged += 1
    return merged


def refresh_cache(force=False):
    """
    确保内存缓存可用：只从本地缓存文件加载，绝不连接 Wind。
    force=True 时忽略内存中已有缓存，强制重新读盘
    （update_daily.py 更新缓存后调用，服务无需重启即可加载新数据）。
    返回 True 表示缓存可用，False 表示无本地缓存（需先运行 update_daily.py）。
    """
    global series_cache
    if force or series_cache is None:
        meta = load_cache_file()
        if meta is None:
            log.warning("本地缓存缺失：请先运行 update_daily.py 更新数据"
                        "（仅更新数据时需要连接 Wind）")
            return False
    return bool(series_cache)


def get_series(sid, days=None):
    """从本地缓存取某指标的序列；days 指定则按日期过滤（None=全量）"""
    if series_cache is None or sid not in series_cache:
        return [], []
    rec = series_cache[sid]
    times = rec.get('times', []) or []
    values = rec.get('values', []) or []
    if not days:
        return times, values
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    out_t, out_v = [], []
    for t, v in zip(times, values):
        if t >= cutoff:
            out_t.append(t)
            out_v.append(v)
    return out_t, out_v

def load_indicator_table():
    global indicators, categories
    indicators.clear()
    categories.clear()

    xls = pd.ExcelFile(EXCEL_PATH)
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet_name, dtype=str)
        for _, row in df.iterrows():
            sid = str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else ''
            level1 = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ''
            level2 = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ''
            name = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ''
            if not sid or sid == 'nan' or sid in EXCLUDED_INDICATORS:
                continue
            indicators[sid] = {'一级': level1, '二级': level2, '指标名称': name, '单位': UNIT_MAP.get(sid, '')}
            if level1 not in categories:
                categories[level1] = OrderedDict()
            if level2 not in categories[level1]:
                categories[level1][level2] = []
            if sid not in categories[level1][level2]:
                categories[level1][level2].append(sid)

    log.info(f"Loaded {len(indicators)} indicators across {len(categories)} categories.")

# ── API Routes ────────────────────────────────────────────

@bp.after_request
def add_no_cache_headers(resp):
    """禁止浏览器缓存页面/接口，避免同事看到旧版本页面（曾两次因缓存误判数据未更新）"""
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@bp.route('/')
def index():
    # 旧版首页已废弃删除；根路径直接跳转到主面板（保留书签/默认网址可用）
    return redirect(url_for('ipm_tracker.whale_dashboard'))


@bp.route('/whale')
def whale_dashboard():
    return render_template('whale-dashboard.html')


@bp.route('/api/indicators')
def get_indicators():
    """返回所有指标分类结构"""
    result = []
    for l1, l2_dict in list(categories.items()):
        l1_item = {'name': l1, 'children': []}
        for l2, ids in list(l2_dict.items()):
            l2_item = {'name': l2, 'indicators': []}
            for sid in ids:
                info = indicators.get(sid, {})
                l2_item['indicators'].append({
                    'id': sid,
                    'name': info.get('指标名称', sid),
                })
            l1_item['children'].append(l2_item)
        result.append(l1_item)
    return jsonify({'status': 'ok', 'data': result, 'total': len(indicators)})


@bp.route('/api/data/latest')
def get_latest_data():
    """获取所有指标的最新数据（最近60天，读本地缓存）"""
    result = {}
    all_ids = list(indicators.keys())

    for sid in all_ids:
        times, vals = get_series(sid, days=60)
        if vals:
            latest_val = vals[-1]
            prev_val = vals[-2] if len(vals) >= 2 else latest_val
        else:
            latest_val = prev_val = None

        info = indicators.get(sid, {})
        result[sid] = {
            'id': sid,
            'name': info.get('指标名称', ''),
            '一级': info.get('一级', ''),
            '二级': info.get('二级', ''),
            '单位': info.get('单位', ''),
            'latest': round(float(latest_val), 4) if latest_val is not None else None,
            'previous': round(float(prev_val), 4) if prev_val is not None else None,
            'change': round(float(latest_val - prev_val), 4) if latest_val is not None and prev_val is not None else None,
            'trend': vals,
        }

    log.info(f"Latest data served from cache for {len(result)} indicators.")
    return jsonify({'status': 'ok', 'data': result, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})


@bp.route('/api/data/<indicator_id>')
def get_indicator_data(indicator_id):
    """获取单个指标的历史数据（近3年，读本地缓存）。
    年频/低频指标（如快递业务平均单价）近3年窗口内可能不足2个点，
    此时回退为2021年以来全量，保证主面板能正常绘制。"""
    times, vals = get_series(indicator_id, days=1095)
    if sum(1 for v in vals if v is not None) < 2:
        times, vals = get_series(indicator_id, days=None)
    if not vals:
        return jsonify({'status': 'error', 'message': f'No cached data for {indicator_id}'}), 404

    info = indicators.get(indicator_id, {})

    return jsonify({
        'status': 'ok',
        'data': {
            'id': indicator_id,
            'name': info.get('指标名称', ''),
            '一级': info.get('一级', ''),
            '二级': info.get('二级', ''),
            '单位': info.get('单位', ''),
            'times': times,
            'values': [round(float(v), 4) if v is not None else None for v in vals],
        }
    })


@bp.route('/api/data/seasonal/<indicator_id>')
def get_seasonal_data(indicator_id):
    """获取单个指标的历史数据（自2021年起，读本地缓存）"""
    times, vals = get_series(indicator_id, days=None)
    if not vals:
        return jsonify({'status': 'error', 'message': f'No cached seasonal data for {indicator_id}'}), 404

    info = indicators.get(indicator_id, {})

    return jsonify({
        'status': 'ok',
        'data': {
            'id': indicator_id,
            'name': info.get('指标名称', ''),
            '一级': info.get('一级', ''),
            '二级': info.get('二级', ''),
            '单位': info.get('单位', ''),
            'times': times,
            'values': [round(float(v), 4) if v is not None else None for v in vals],
        }
    })


@bp.route('/api/category/<category>')
def get_category_data(category):
    """获取某个一级分类下所有指标的最新数据（近180天，读本地缓存）"""
    result = {}
    for l1, l2_dict in list(categories.items()):
        if l1 == category:
            for l2, ids in list(l2_dict.items()):
                for sid in ids:
                    times, vals = get_series(sid, days=180)
                    info = indicators.get(sid, {})
                    result[sid] = {
                        'id': sid,
                        'name': info.get('指标名称', ''),
                        '二级': info.get('二级', ''),
                        'latest': round(float(vals[-1]), 4) if vals else None,
                        'times': times,
                        'values': [round(float(v), 4) if v is not None else None for v in vals],
                    }
            break

    return jsonify({'status': 'ok', 'data': result, 'category': category})


@bp.route('/api/health')
def health():
    cache_meta = load_cache_file()
    return jsonify({
        'status': 'ok',
        # 网站服务不连接 Wind；数据更新由 update_daily.py 负责
        'wind_required': False,
        'data_source': 'local_cache',
        'indicators_loaded': len(indicators),
        'categories': len(categories),
        'cache': {
            'enabled': True,
            'updated': cache_meta.get('updated') if cache_meta else None,
            'cached_indicators': len(series_cache) if series_cache else 0,
            'file': CACHE_FILE,
        },
    })


@bp.route('/api/data/refresh', methods=['POST'])
def refresh_data():
    """重新从磁盘加载本地缓存（不连接 Wind）。
    数据更新流程：先运行 update_daily.py（仅此步需要 Wind），
    再调用本接口，运行中的服务即可立即读到新数据，无需重启。"""
    ok = refresh_cache(force=True)
    if not ok:
        return jsonify({'status': 'error',
                        'message': '本地缓存缺失，请先运行 update_daily.py 更新数据'}), 404
    return jsonify({'status': 'ok', 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})


def _extract_upload_password():
    """从表单 / 请求头 / JSON body 中提取上传口令"""
    pw = request.form.get('password') if request.form else None
    if pw:
        return pw
    pw = request.headers.get('X-Upload-Password')
    if pw:
        return pw
    if request.is_json:
        body = request.get_json(silent=True) or {}
        return body.get('password', '')
    return ''


@bp.route('/api/data/upload', methods=['POST'])
def upload_data():
    """网页上传数据更新文件（需口令；供无法直连 Wind 的部署点更新数据）。

    支持两类文件（自动识别）：
      - 增量文件 data\\YYYYMMDD.json（update_daily.py 生成）→ 合并进本地缓存，
        并按原日期归档到 data\\ 目录（保持 /data/ 页面与增量链完整）
      - 全量缓存 cache\\indicators_data.json → 整体替换本地缓存
    调用方式：multipart 表单（file + password 字段），或 JSON body
    （{"password": "...", ...增量/全量结构}）。
    """
    return jsonify({
        'status': 'error',
        'message': '页面上传已停用，请在内部研究工作台后台执行一键更新。',
    }), 410

    global series_cache

    # 读取上传内容
    payload = None
    f = request.files.get('file')
    if f is not None:
        try:
            payload = json.loads(f.read().decode('utf-8-sig'))
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'文件不是有效的 JSON：{e}'}), 400
    elif request.is_json:
        payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'status': 'error', 'message': '未收到有效的数据文件'}), 400

    inc = payload.get('indicators')
    if not isinstance(inc, dict) or not inc:
        return jsonify({'status': 'error', 'message': '文件缺少 indicators 数据'}), 400
    for sid, rec in list(inc.items())[:5]:
        if not isinstance(rec, dict) or not isinstance(rec.get('times'), list) \
                or not isinstance(rec.get('values'), list):
            return jsonify({'status': 'error',
                            'message': f'指标 {sid} 结构异常（需要 times/values 数组）'}), 400

    # 自动识别：增量文件（带 date/start/end 字段）或全量缓存
    is_increment = isinstance(payload.get('date'), str) and 'start' in payload and 'end' in payload
    inc_date = None

    with _cache_lock:
        if is_increment:
            merged = merge_increment_into_cache(inc)
            # 归档增量文件（沿用原日期命名）
            date_str = payload['date']
            if not (len(date_str) == 8 and date_str.isdigit()):
                date_str = datetime.now().strftime('%Y%m%d')
                payload['date'] = date_str
            inc_date = date_str
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, f'{date_str}.json'), 'w', encoding='utf-8') as fp:
                json.dump(payload, fp, ensure_ascii=False)
            mode_desc = f'增量合并 {date_str}（{merged} 个指标）'
        else:
            series_cache = inc
            mode_desc = '全量替换'
        save_cache_file()
        load_cache_file()  # 重新读盘，确保服务内容与磁盘一致

    log.info(f"Upload from {request.remote_addr}: {mode_desc}, file holds {len(inc)} indicators")
    return jsonify({
        'status': 'ok',
        'mode': 'increment' if is_increment else 'full',
        'increment_date': inc_date,
        'indicators': len(inc),
        'cache_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@bp.route('/api/data/status')
def data_status():
    """数据新鲜度状态（供大网页集成显示）：
    - last_increment: data 目录最近一次增量文件日期（YYYYMMDD）
    - increment_files: 全部增量文件清单
    - cache_updated: 主缓存最近更新时间
    - data_latest_date: 所有指标中最后的数据日期（反映数据真正新鲜度）
    """
    inc_files = []
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            stem = os.path.splitext(name)[0]
            if len(stem) == 8 and stem.isdigit():
                inc_files.append(stem)
    inc_files.sort()

    last_data_date = None
    for rec in (series_cache or {}).values():
        times = rec.get('times') or []
        if times:
            last_data_date = max(last_data_date or '', times[-1])

    cache_meta = load_cache_file()
    return jsonify({
        'status': 'ok',
        'cache_updated': cache_meta.get('updated') if cache_meta else None,
        'last_increment': inc_files[-1] if inc_files else None,
        'increment_files': inc_files,
        'data_latest_date': last_data_date,
        'indicators': len(series_cache) if series_cache else 0,
    })


# /data/ 页面的上传脚本（普通字符串，避免 f-string 转义 JS 花括号）
_DATA_PAGE_UPLOAD_SCRIPT = """<script>
async function doUpload(){
  const res = document.getElementById('ures');
  const pw = document.getElementById('pw').value;
  const f = document.getElementById('uf').files[0];
  if(!pw || !f){ res.textContent = '请输入口令并选择文件'; res.style.color = '#dc2626'; return; }
  const fd = new FormData();
  fd.append('password', pw);
  fd.append('file', f);
  res.textContent = '上传中...'; res.style.color = '#6b7280';
  try{
    const r = await fetch('/api/data/upload', {method: 'POST', body: fd});
    const j = await r.json();
    if(j.status === 'ok'){
      const incTxt = j.mode === 'increment' ? ('增量 ' + (j.increment_date || '') + ' 已生效') : '全量替换完成';
      res.textContent = '更新成功（' + incTxt + '，' + j.indicators + ' 个指标），即将刷新...';
      res.style.color = '#059669';
      setTimeout(() => location.reload(), 1200);
    } else {
      res.textContent = j.message || '上传失败';
      res.style.color = '#dc2626';
    }
  }catch(e){
    res.textContent = '网络错误: ' + e.message;
    res.style.color = '#dc2626';
  }
}
</script>"""


@bp.route('/data/')
def data_files():
    """增量数据文件浏览页：同事可通过 http://IP/data/ 查看/下载所有 data\\YYYYMMDD.json"""
    return jsonify({'status': 'error', 'message': '数据文件管理已移至门户后台。'}), 410

    import html as _html
    inc_files = []
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            stem = os.path.splitext(name)[0]
            if len(stem) == 8 and stem.isdigit():
                p = os.path.join(DATA_DIR, name)
                inc_files.append({
                    'date': stem,
                    'size': os.path.getsize(p),
                    'mtime': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d %H:%M:%S'),
                })
    inc_files.sort(key=lambda x: x['date'], reverse=True)

    cache_meta = load_cache_file()
    rows = []
    for f in inc_files:
        rows.append(
            '<tr>'
            f'<td class="mono">{f["date"]}</td>'
            f'<td class="mono">{f["mtime"]}</td>'
            f'<td class="mono">{f["size"]:,} B</td>'
            f'<td><a href="/data/{f["date"]}.json" download>下载</a> · '
            f'<a href="/data/{f["date"]}.json" target="_blank">查看</a></td>'
            '</tr>'
        )
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>行业景气高频跟踪 · 数据文件</title><style>
body{{font-family:'Segoe UI',Microsoft YaHei,sans-serif;background:#f5f7fa;margin:0;padding:40px}}
.card{{background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:860px;margin:0 auto;padding:28px 36px}}
h1{{font-size:22px;color:#1f2937;margin:0 0 6px}}
.sub{{color:#6b7280;font-size:13px;margin-bottom:22px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:#6b7280;font-weight:600;padding:10px 8px;border-bottom:2px solid #e5e7eb}}
td{{padding:10px 8px;border-bottom:1px solid #f0f1f3;color:#1f2937}}
tr:hover td{{background:#f9fafb}}
.mono{{font-family:Consolas,'Courier New',monospace}}
a{{color:#2563eb;text-decoration:none}} a:hover{{text-decoration:underline}}
.badge{{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:20px;padding:3px 12px;font-size:12px;margin-left:8px}}
.api{{background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;font-size:13px;color:#374151;margin:6px 0 18px}}
.api code{{background:#eef2ff;color:#4338ca;padding:1px 6px;border-radius:4px}}
.empty{{color:#9ca3af;text-align:center;padding:30px}}
</style></head><body><div class="card">
<h1>行业景气高频跟踪 · 数据文件<span class="badge">{len(inc_files)} 个增量文件</span></h1>
<div class="sub">主缓存更新于 {cache_meta.get('updated', '未知') if cache_meta else '未知'} · 每个文件为一次增量更新（81 指标）</div>
<div class="api">程序获取方式（任选其一）：<br>
① 下载单个文件：<code>curl http://IP/data/YYYYMMDD.json</code><br>
② 最新数据接口：<code>curl http://IP/api/data/status</code>（含最近更新日期）<br>
③ 全量数据：<code>curl http://IP/api/data/latest</code>（近60天）</div>
<table><tr><th>日期</th><th>更新时间</th><th>大小</th><th>操作</th></tr>
{''.join(rows) if rows else '<tr><td colspan="4" class="empty">暂无增量文件</td></tr>'}
</table>
<div class="api" style="margin-top:18px">网页更新入口（需口令）：上传 update_daily.py 生成的增量文件（data\\YYYYMMDD.json）或全量缓存（cache\\indicators_data.json）<br>
<input type="password" id="pw" placeholder="口令" autocomplete="off" style="padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;margin-top:8px">
<input type="file" id="uf" accept=".json" style="margin-top:8px">
<button onclick="doUpload()" style="padding:6px 16px">上传更新</button>
<span id="ures" style="margin-left:8px;font-size:13px"></span></div>
</div></body></html>"""
    return page + _DATA_PAGE_UPLOAD_SCRIPT


@bp.route('/data/<filename>')
def data_file(filename):
    return jsonify({'status': 'error', 'message': '数据文件管理已移至门户后台。'}), 410

    """下载/查看指定增量文件（仅允许 8 位数字日期命名的 .json，防路径穿越）"""
    if not (len(filename) == 13 and filename[:8].isdigit() and filename.endswith('.json')):
        return jsonify({'status': 'error', 'message': 'invalid filename'}), 400
    p = os.path.join(DATA_DIR, filename)
    if not os.path.isfile(p):
        return jsonify({'status': 'error', 'message': 'file not found'}), 404
    return send_file(p, as_attachment=False, mimetype='application/json')


# ── 异动预警 ──────────────────────────────────────────────
# 规则（对 2021 年以来全量缓存序列统计；数据来自网页上传的更新文件合并缓存，无需 Wind）：
#   1. 变动 Z-score：最新一期变动相对历史逐期变动的偏离，|z|≥2 触发，≥2.5 红牌权重
#   2. 历史分位：最新值处于 2021 年以来分位 ≥90%/≤10% 触发，≥95%/≤5% 加权
#   3. 久违新高/新低：创 2021 年以来新高/低，且前一极值出现在 ≥12 期之前
#      （排除趋势性序列"天天新高"的噪音）
#   4. 连续同向：连续 3~8 期同方向变动（>8 期视为结构性趋势，不再报）
# 记分制判级：z≥2.5→+3；久违新高/低→+2；分位极端→+1；2σ~2.5σ→+1.5；
# 分位 90/10→+0.5；连续同向→+1。总分 ≥3 判红，否则黄牌。
ALERT_Z_YELLOW = 2.0
ALERT_Z_RED = 2.5
ALERT_PCT_YELLOW = 10.0   # 分位 ≤10% 或 ≥90%
ALERT_PCT_RED = 5.0       # 分位 ≤5% 或 ≥95%
ALERT_STREAK_MIN = 3
ALERT_STREAK_MAX = 8      # 连续同向超过 8 期视为趋势，不再作为异动信号
ALERT_EXTREME_GAP = 12    # 前一历史极值距今 ≥12 期，才算"久违"新高/新低
ALERT_MIN_OBS = 8         # 样本不足 8 期的指标不参与巡检


def _analyze_indicator(sid):
    """对单个指标做异动统计，返回分析 dict（含 signals/score/level）或 None（样本不足/无缓存）"""
    times, values = get_series(sid, days=None)
    pairs = []
    for t, v in zip(times, values):
        if v is None:
            continue
        try:
            pairs.append((t, float(v)))
        except (TypeError, ValueError):
            continue
    if len(pairs) < ALERT_MIN_OBS:
        return None

    vals = [v for _, v in pairs]
    latest_t, latest = pairs[-1]
    prev = vals[-2]
    change = latest - prev
    change_pct = (change / abs(prev) * 100.0) if prev != 0 else None

    diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    mean_d = sum(diffs) / len(diffs)
    var_d = sum((d - mean_d) ** 2 for d in diffs) / max(len(diffs) - 1, 1)
    std_d = var_d ** 0.5
    zscore = (change - mean_d) / std_d if std_d > 0 else 0.0

    percentile = sum(1 for v in vals if v <= latest) / len(vals) * 100.0

    hist_max_prev = max(vals[:-1])
    hist_min_prev = min(vals[:-1])
    # 前一历史极值距今的期数：趋势性序列每期都在刷新极值（间隔 1 期），不构成异动；
    # 只有"时隔较久再创新高/低"才有信息量（降噪）
    is_high = is_low = False
    if latest > hist_max_prev:
        prev_max_idx = max(i for i in range(len(vals) - 1) if vals[i] == hist_max_prev)
        if len(vals) - 1 - prev_max_idx >= ALERT_EXTREME_GAP:
            is_high = True
    if latest < hist_min_prev:
        prev_min_idx = max(i for i in range(len(vals) - 1) if vals[i] == hist_min_prev)
        if len(vals) - 1 - prev_min_idx >= ALERT_EXTREME_GAP:
            is_low = True

    streak = 0
    if change > 0:
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] > vals[i - 1]:
                streak += 1
            else:
                break
    elif change < 0:
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] < vals[i - 1]:
                streak += 1
            else:
                break

    signals, score = [], 0.0
    az = abs(zscore)
    if az >= ALERT_Z_RED:
        signals.append(f'变动异常 {zscore:+.1f}σ')
        score += 3
    elif az >= ALERT_Z_YELLOW:
        signals.append(f'变动偏大 {zscore:+.1f}σ')
        score += 1.5
    if percentile >= 100 - ALERT_PCT_RED or percentile <= ALERT_PCT_RED:
        signals.append(f'历史分位 {percentile:.0f}%（极端）')
        score += 1
    elif percentile >= 100 - ALERT_PCT_YELLOW or percentile <= ALERT_PCT_YELLOW:
        signals.append(f'历史分位 {percentile:.0f}%')
        score += 0.5
    if is_high:
        signals.append(f'时隔≥{ALERT_EXTREME_GAP}期再创新高')
        score += 2
    if is_low:
        signals.append(f'时隔≥{ALERT_EXTREME_GAP}期再创新低')
        score += 2
    if ALERT_STREAK_MIN <= streak <= ALERT_STREAK_MAX:
        signals.append(f'连续{streak}期{"上行" if change > 0 else "下行"}')
        score += 1

    info = indicators.get(sid, {})
    try:
        age_days = (datetime.now() - datetime.strptime(latest_t, '%Y-%m-%d')).days
    except ValueError:
        age_days = None

    return {
        'id': sid,
        'name': info.get('指标名称', sid),
        '一级': info.get('一级', ''),
        '二级': info.get('二级', ''),
        '单位': info.get('单位', ''),
        'latest': round(latest, 4),
        'latest_date': latest_t,
        'age_days': age_days,
        'change': round(change, 4),
        'change_pct': round(change_pct, 2) if change_pct is not None else None,
        'direction': 'up' if change > 0 else ('down' if change < 0 else 'flat'),
        'zscore': round(zscore, 2),
        'percentile': round(percentile, 1),
        'streak': streak,
        'signals': signals,
        'level': 'red' if score >= 3 else 'yellow',
        'score': score,
        'trend': [round(v, 4) for v in vals[-40:]],
    }


def _latest_increment_sids():
    """最近一次上传增量文件的 (date_str, start, end, 指标id集合)；无则 (None,None,None,set())"""
    if not os.path.isdir(DATA_DIR):
        return None, None, None, set()
    names = [os.path.splitext(n)[0] for n in os.listdir(DATA_DIR)
             if len(os.path.splitext(n)[0]) == 8 and os.path.splitext(n)[0].isdigit()]
    if not names:
        return None, None, None, set()
    latest = sorted(names)[-1]
    try:
        with open(os.path.join(DATA_DIR, f'{latest}.json'), 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return latest, payload.get('start'), payload.get('end'), set((payload.get('indicators') or {}).keys())
    except Exception:
        return latest, None, None, set()


@bp.route('/api/alerts')
def api_alerts():
    """全部指标异动巡检结果（供 /alerts 预警页）：
    - alerts: 触发信号的指标，按严重度排序（含行业归属、信号明细、近40期走势、in_last_batch）
    - industries: 各一级行业概览（指标数/本期更新数/涨跌家数/红黄牌数）
    """
    inc_date, inc_start, inc_end, inc_sids = _latest_increment_sids()

    alerts = []
    industry_stats = {}
    checked = 0
    for sid, info in indicators.items():
        checked += 1
        l1 = info.get('一级', '') or '未分类'
        st = industry_stats.setdefault(l1, {
            'name': l1, 'total': 0, 'fresh': 0, 'up': 0, 'down': 0, 'flat': 0,
            'red': 0, 'yellow': 0,
        })
        st['total'] += 1
        a = _analyze_indicator(sid)
        if a is None:
            continue
        if sid in inc_sids:
            st['fresh'] += 1
        st[a['direction']] = st.get(a['direction'], 0) + 1
        if a['signals']:
            a['in_last_batch'] = sid in inc_sids
            alerts.append(a)
            st[a['level']] += 1

    alerts.sort(key=lambda x: (-x['score'], x['一级'], x['二级']))
    red = sum(1 for a in alerts if a['level'] == 'red')
    # 行业顺序沿用指标表顺序（消费、金融地产、科技、制造、周期、公用）
    order = {name: i for i, name in enumerate(categories.keys())}
    industries = sorted(industry_stats.values(), key=lambda x: order.get(x['name'], 99))

    last_data_date = None
    for rec in (series_cache or {}).values():
        times = rec.get('times') or []
        if times:
            last_data_date = max(last_data_date or '', times[-1])

    return jsonify({
        'status': 'ok',
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'last_increment': inc_date,
        'increment_range': [inc_start, inc_end],
        'data_latest_date': last_data_date,
        'total_indicators': checked,
        'alerted': len(alerts),
        'red_count': red,
        'yellow_count': len(alerts) - red,
        'industries': industries,
        'alerts': alerts,
        'rules': {
            'z_yellow': ALERT_Z_YELLOW, 'z_red': ALERT_Z_RED,
            'pct_yellow': ALERT_PCT_YELLOW, 'pct_red': ALERT_PCT_RED,
            'streak_min': ALERT_STREAK_MIN, 'streak_max': ALERT_STREAK_MAX,
            'extreme_gap': ALERT_EXTREME_GAP,
        },
    })


@bp.route('/alerts')
def alerts_page():
    return render_template('alerts.html')


def init_app():
    """初始化行业景气高频跟踪模块，不在网页请求期间连接 Wind。"""
    ensure_runtime_data()
    load_indicator_table()
    if not refresh_cache(force=True):
        log.warning("行业景气高频跟踪缓存为空，请在门户后台执行一键更新。")
