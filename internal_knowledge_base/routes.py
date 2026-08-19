# -*- coding: utf-8 -*-
"""内部知识库 Flask Blueprint。"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import (Blueprint, current_app, request, send_file, send_from_directory,
                   jsonify, session, redirect, url_for, abort, Response)
from werkzeug.security import check_password_hash, generate_password_hash

from paths import (INTERNAL_KNOWLEDGE_BASE_DIR, INTERNAL_KNOWLEDGE_BASE_DB,
                   INTERNAL_KNOWLEDGE_BASE_UPLOADS, INTERNAL_KNOWLEDGE_BASE_PDF_CACHE,
                   INTERNAL_KNOWLEDGE_BASE_TEMP)
from .storage import SQLiteStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = str(INTERNAL_KNOWLEDGE_BASE_DIR)
CACHE_DIR = str(INTERNAL_KNOWLEDGE_BASE_TEMP)
PREVIEW_CACHE_DIR = str(INTERNAL_KNOWLEDGE_BASE_PDF_CACHE)
UPLOAD_DIR = str(INTERNAL_KNOWLEDGE_BASE_UPLOADS)
STORE_PATH = str(INTERNAL_KNOWLEDGE_BASE_DB)
CONVERSION_VERSION = "libreoffice-v1"

CST = timezone(timedelta(hours=8))

# 可转换的文件扩展名
CONVERTIBLE_EXTS = {".pptx", ".ppt", ".doc", ".docx", ".xls", ".xlsx"}
ALLOWED_UPLOAD_EXTS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"}
MAX_UPLOAD_SIZE = 100 * 1024 * 1024
DEFAULT_USER_PASSWORD = "123456"

# 报告主题分类（与前端 js/data.js 中 REPORT_THEMES 保持一致）
REPORT_THEMES = (
    "macro_rate", "credit", "multi_asset", "quant", "fixed_income_plus",
    "equity", "other",
)
REPORT_THEME_LABELS = {
    "macro_rate": "宏观利率", "credit": "信用", "multi_asset": "多资产", "quant": "量化",
    "fixed_income_plus": "固收+", "equity": "权益", "other": "其他",
}
REPORT_CATEGORY_LABELS = {
    "weekly": "周报", "monthly": "月报", "deep": "深度报告", "other": "其他报告",
}


def _default_reminder_config():
    """附件《报告要求.xlsx》对应的默认专题报告规则。"""
    return {
        "period": datetime.now(CST).strftime("%Y"),
        "reportCategory": "deep",
        "rules": [
            {"id": "fi-credit", "label": "固收中心 · 信用组", "mode": "group", "target": 2,
             "userIds": ["huyongyan", "zhenghongbin"]},
            {"id": "fi-multi-asset", "label": "固收中心 · 多资产组", "mode": "group", "target": 2,
             "userIds": ["qianyouni", "cuizhuoju", "zhangyunfan"]},
            {"id": "fi-quant", "label": "固收中心 · 量化组", "mode": "group", "target": 2,
             "userIds": ["zhengmeng", "maxiao", "ouyangquan"]},
            {"id": "fi-plus", "label": "固收中心 · 固收+组", "mode": "group", "target": 2,
             "userIds": ["maying", "linyuang"]},
            {"id": "fi-macro-rate", "label": "固收中心 · 宏观利率组", "mode": "group", "target": 2,
             "userIds": ["kanghairong", "zhumeng"]},
            {"id": "aa-feilixiao", "label": "资产配置部研究组 · 费立孝", "mode": "person", "target": 2,
             "userIds": ["feilixiao"]},
            {"id": "aa-zhangqingchang", "label": "资产配置部研究组 · 张庆昌", "mode": "person", "target": 2,
             "userIds": ["zhangqingchang"]},
            {"id": "aa-wanghui", "label": "资产配置部研究组 · 王辉", "mode": "person", "target": 2,
             "userIds": ["wanghui"]},
            {"id": "aa-liushengyao", "label": "资产配置部研究组 · 刘圣尧", "mode": "person", "target": 2,
             "userIds": ["liushengyao"]},
            {"id": "aa-zongshaohui", "label": "资产配置部研究组 · 宗韶晖", "mode": "person", "target": 2,
             "userIds": ["zongshaohui"]},
            {"id": "aa-songlingfeng", "label": "资产配置部研究组 · 宋凌峰", "mode": "person", "target": 2,
             "userIds": ["songlingfeng"]},
        ],
    }

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# DeepSeek LLM 配置（智能补全关键词与摘要）
# 密钥不写入文件：优先读环境变量，Windows 下回退读注册表 HKCU\Environment
# --------------------------------------------------------------------------- #
def _llm_api_key():
    """Resolve the DeepSeek API key without persisting it in project files."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key or os.name != "nt":
        return key
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env_key:
            return str(winreg.QueryValueEx(env_key, "DEEPSEEK_API_KEY")[0]).strip()
    except OSError:
        return ""


LLM_BASE_URL = "https://api.deepseek.com"
LLM_MODEL = "deepseek-chat"
LLM_TIMEOUT = 90
LLM_MAX_TEXT_CHARS = 6000  # 发送给 LLM 的文档文本最大长度


# --------------------------------------------------------------------------- #
# LibreOffice 查找与转换
# --------------------------------------------------------------------------- #
def find_soffice():
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    for name in ("soffice", "soffice.com", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


SOFFICE = find_soffice()


def source_hash(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(file_path, file_size=None):
    digest = source_hash(file_path)
    return hashlib.sha256(f"{CONVERSION_VERSION}:{digest}".encode("utf-8")).hexdigest()


def convert_to_pdf(input_path, output_dir):
    # 每次转换使用独立 LibreOffice 用户配置，避免桌面 Office/并发预览导致
    # soffice 复用或锁定全局配置后无输出退出。配置目录随预览临时目录清理。
    profile_dir = os.path.join(output_dir, ".lo_profile")
    os.makedirs(profile_dir, exist_ok=True)
    profile_uri = Path(profile_dir).resolve().as_uri()
    cmd = [SOFFICE, f"-env:UserInstallation={profile_uri}",
           "--headless", "--norestore", "--nolockcheck",
           "--convert-to", "pdf", "--outdir", output_dir, input_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice 转换失败: {result.stderr or result.stdout}")
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    pdf_path = os.path.join(output_dir, base_name + ".pdf")
    if not os.path.isfile(pdf_path):
        raise RuntimeError("转换后未找到 PDF 文件")
    return pdf_path


def _remove_preview_artifacts(path, retries=30, retry_delay=1):
    """删除单次预览产生的临时目录；Windows 文件句柄未释放时自动重试。"""
    for _ in range(retries):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            return True
        except OSError:
            time.sleep(retry_delay)
    return False


def _schedule_preview_cleanup(path):
    """响应完成即清理，并提供后台重试兜底，最迟约 30 秒再次尝试。"""
    worker = threading.Thread(target=_remove_preview_artifacts, args=(path,), daemon=True)
    worker.start()


def _send_temporary_pdf(pdf_path, work_dir):
    response = send_file(pdf_path, mimetype="application/pdf", as_attachment=False)
    response.call_on_close(lambda: _remove_preview_artifacts(work_dir, retries=1, retry_delay=0))
    _schedule_preview_cleanup(work_dir)
    return response


def _make_preview_work_dir():
    work_dir = os.path.join(CACHE_DIR, f"preview_{uuid.uuid4().hex}")
    os.makedirs(work_dir, exist_ok=False)
    return work_dir


def clear_preview_cache():
    """启动时清除上次异常退出可能遗留的一次性预览临时目录。

    正式 PDF 缓存位于独立目录，不参与启动清理并永久保留，直至超级管理员删除。
    """
    removed = 0
    if not os.path.isdir(CACHE_DIR):
        return removed
    for name in os.listdir(CACHE_DIR):
        if name == os.path.basename(PREVIEW_CACHE_DIR):
            continue  # 跳过持久化 PDF 缓存目录
        path = os.path.join(CACHE_DIR, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def _get_cached_pdf(file_path):
    """返回永久缓存中的 PDF；缓存不按时间自动过期。"""
    key = cache_key(file_path)
    cached = os.path.join(PREVIEW_CACHE_DIR, f"{key}.pdf")
    if not os.path.isfile(cached):
        if store.get_pdf_cache(key):
            store.delete_pdf_cache(key)
        return None
    store.touch_pdf_cache(key)
    return cached


def _store_cached_pdf(src_pdf, file_path, report_id=None):
    """将转换产物落盘到持久缓存，返回缓存路径；失败时返回 None 不影响预览。"""
    key = cache_key(file_path)
    cached = os.path.join(PREVIEW_CACHE_DIR, f"{key}.pdf")
    try:
        # 同目录 move 最快；跨目录先 copy 再清理源
        if os.path.abspath(os.path.dirname(src_pdf)) == os.path.abspath(PREVIEW_CACHE_DIR):
            cached = src_pdf
        else:
            shutil.move(src_pdf, cached)
        store.upsert_pdf_cache(
            key, report_id, source_hash(file_path), os.path.basename(cached),
            os.path.getsize(cached), CONVERSION_VERSION,
        )
        return cached
    except OSError:
        return None


def _preview_cache_cleanup_loop():
    """兼容旧调用；正式 PDF 缓存只允许超级管理员删除。"""
    return None


def _delete_cache_files(cache_keys):
    deleted = 0
    root = Path(PREVIEW_CACHE_DIR).resolve()
    for key in cache_keys:
        if not re.fullmatch(r"[0-9a-f]{64}", str(key)):
            continue
        path = (root / f"{key}.pdf").resolve()
        if root not in path.parents:
            continue
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            current_app.logger.exception("删除 PDF 缓存失败: %s", key)
    return deleted


# --------------------------------------------------------------------------- #
# 数据持久化
# --------------------------------------------------------------------------- #
def _now_iso():
    return datetime.now(CST).isoformat(timespec="seconds")


def _infer_theme(report):
    """根据报告标题、摘要、标签推断主题分类（用于迁移旧数据）。"""
    text = " ".join([
        report.get("title", ""),
        report.get("summary", ""),
        " ".join(report.get("tags") or []),
    ]).lower()
    if any(k in text for k in ["信用", "credit", "利差"]):
        return "credit"
    if any(k in text for k in ["固收+", "固收加", "fixed_income_plus"]):
        return "fixed_income_plus"
    if any(k in text for k in ["多资产", "multi_asset", "股债"]):
        return "multi_asset"
    if any(k in text for k in ["量化", "quant", "模型"]):
        return "quant"
    if any(k in text for k in ["权益", "equity", "股票", "行业"]):
        return "equity"
    if any(k in text for k in ["宏观", "利率", "macro", "经济周期", "利率债"]):
        return "macro_rate"
    return "other"


clear_preview_cache()
store = SQLiteStore(Path(STORE_PATH), _default_reminder_config())
bp = Blueprint("internal_knowledge_base", __name__)
app = bp  # Keep existing route declarations compact.


def csrf_token():
    token = session.get("internal_knowledge_base_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["internal_knowledge_base_csrf"] = token
    return token


@bp.before_request
def protect_internal_knowledge_base():
    if not session.get("authenticated"):
        if "/api/" in request.path:
            return jsonify({"error": "请先登录内部研究平台", "portalAuthRequired": True}), 401
        return redirect(url_for("login", next=request.full_path.rstrip("?")))
    if request.content_length and request.content_length > MAX_UPLOAD_SIZE + 1024 * 1024:
        return jsonify({"error": "上传文件超过100MB限制"}), 413
    if request.method not in {"GET", "HEAD", "OPTIONS"} and "/api/" in request.path:
        submitted = request.headers.get("X-CSRF-Token", "")
        expected = session.get("internal_knowledge_base_csrf", "")
        if not expected or not secrets.compare_digest(submitted, expected):
            return jsonify({"error": "安全令牌已失效，请刷新页面后重试"}), 403


@bp.after_request
def audit_mutation(response):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and "/api/" in request.path and response.status_code < 400:
        actor_id = session.get("internal_knowledge_base_user_id")
        actor_type = "superadmin" if session.get("internal_knowledge_base_superadmin") else "user"
        try:
            store.audit(actor_type, actor_id, request.endpoint or request.path,
                        "request", next(iter((request.view_args or {}).values()), None),
                        {"method": request.method}, request.remote_addr)
        except Exception:
            current_app.logger.exception("内部知识库审计日志写入失败")
    return response

# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #
def public_user(user):
    """对外返回用户信息（不含密码）。"""
    if not user:
        return None
    return {k: user.get(k) for k in ("id", "name", "org", "role")}


def public_report(report):
    """对外返回报告信息（不含内部字段）。"""
    if not report:
        return None
    return dict(report)


SCORING_ORGS = ("资产配置部", "固收中心")


def report_scoring_orgs(report):
    """返回报告的打分部门列表；未配置时默认两部门均打分（向后兼容）。"""
    orgs = report.get("scoringOrgs")
    if not isinstance(orgs, list) or not orgs:
        return list(SCORING_ORGS)
    return [org for org in orgs if org in SCORING_ORGS] or list(SCORING_ORGS)


def leader_in_scoring_scope(user, scoring_orgs):
    """部门领导按报告所选打分部门过滤；不隶属具体打分部门的通用领导始终参与。"""
    org = user.get("org")
    if org in SCORING_ORGS:
        return org in scoring_orgs
    return True


def eligible_scorers(report):
    """返回某份月报/深度报告的应评分人员列表。

    规则：部门领导（org 为资产配置部/固收中心）仅当其所属部门在报告所选打分
    部门内时参与；通用领导（org 为领导/行政/空）始终参与；研究人员仅当其所属
    部门在报告所选打分部门内时参与。行政账号不参与评分。报告对所有人可见，
    本函数仅决定评分资格。
    """
    scoring_orgs = set(report_scoring_orgs(report))
    result = []
    for user in store.users():
        role = user.get("role")
        if role == "admin":
            continue
        if role == "leader":
            if leader_in_scoring_scope(user, scoring_orgs):
                result.append(user)
        elif user.get("org") in scoring_orgs:
            result.append(user)
    return result


def is_eligible_scorer(report, user):
    """判断指定用户是否具备该报告的评分资格。"""
    if not user or user.get("role") == "admin":
        return False
    if user.get("role") == "leader":
        return leader_in_scoring_scope(user, set(report_scoring_orgs(report)))
    return user.get("org") in set(report_scoring_orgs(report))


def report_file_path(report):
    """返回报告文件的安全绝对路径；文件无效时返回空串。"""
    rel = str(report.get("fileUrl", ""))
    if not rel:
        return ""
    upload_root = Path(UPLOAD_DIR).resolve()
    full = (upload_root / Path(rel).name).resolve()
    if upload_root not in full.parents:
        return ""
    return str(full) if full.is_file() else ""


def format_bytes(size):
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "—"
    if size <= 0:
        return "0 KB"
    import math
    units = ["B", "KB", "MB", "GB"]
    idx = min(int(math.log(size, 1024)), len(units) - 1)
    val = size / (1024 ** idx)
    fmt = f"{val:.1f}" if idx > 1 else f"{val:.0f}"
    if fmt.endswith(".0"):
        fmt = fmt[:-2]
    return f"{fmt} {units[idx]}"


def require_user():
    """要求普通用户已登录，返回用户对象或 None。"""
    uid = session.get("internal_knowledge_base_user_id")
    if not uid:
        return None
    return store.get_user(uid)


def require_role(*roles):
    user = require_user()
    if not user or user.get("role") not in roles:
        return None
    return user


def require_admin():
    return True if session.get("internal_knowledge_base_superadmin") else None


def safe_filename(name):
    """把文件名清洗为安全的文件系统名（保留中文）。"""
    name = os.path.basename(name)
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    return name.strip().strip(".") or "upload"


def stored_filename(report_id, original_name):
    """生成上传文件在 uploads/ 下的保存名。"""
    return f"{report_id}__{safe_filename(original_name)}"


# --------------------------------------------------------------------------- #
# 文档文本抽取 & DeepSeek 智能补全
# --------------------------------------------------------------------------- #
def _extract_text(file_path):
    """从 PDF/DOCX/PPTX/XLSX 中抽取纯文本，截断到 LLM_MAX_TEXT_CHARS。
    抽取失败时返回空串，由调用方用文件名兜底。
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            import pymupdf
            parts = []
            with pymupdf.open(file_path) as doc:
                for page in doc:
                    parts.append(page.get_text("text"))
                    if sum(len(p) for p in parts) > LLM_MAX_TEXT_CHARS:
                        break
            text = "\n".join(parts)
        elif ext == ".docx":
            import docx
            doc = docx.Document(file_path)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext in (".pptx", ".ppt"):
            import pptx
            prs = pptx.Presentation(file_path)
            parts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        parts.append(shape.text_frame.text)
                if sum(len(p) for p in parts) > LLM_MAX_TEXT_CHARS:
                    break
            text = "\n".join(parts)
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        parts.append("\t".join(cells))
                    if sum(len(p) for p in parts) > LLM_MAX_TEXT_CHARS:
                        break
                if sum(len(p) for p in parts) > LLM_MAX_TEXT_CHARS:
                    break
            wb.close()
            text = "\n".join(parts)
        else:
            text = ""
    except Exception:
        text = ""
    return text[:LLM_MAX_TEXT_CHARS]


def _call_deepseek(prompt, system=None, max_tokens=None):
    """调用 DeepSeek（OpenAI 兼容接口），返回 message content 字符串。

    system：可选的系统消息，用于约束角色与回答风格。
    max_tokens：可选的输出长度上限，未传时由模型默认值决定。
    两参数均不影响不传时的原有行为（如标签/摘要提取）。
    """
    # 每次调用时解析密钥：Windows 服务更新环境变量并重启、或管理员补充 .env
    # 后，不会因为模块导入时缓存了空值而一直不可用。
    api_key = _llm_api_key()
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    # 系统环境可能配置了失效的本地代理（例如 127.0.0.1:9）。
    # DeepSeek 请求显式直连，避免知识搜索因代理拒绝连接而失败。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"知识服务返回 HTTP {exc.code}{(': ' + detail) if detail else ''}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise RuntimeError("知识服务响应超时，请稍后重试") from exc
        raise RuntimeError("无法连接知识服务，请检查网络连接或 DeepSeek API 配置") from exc
    return data["choices"][0]["message"]["content"]


def _ai_complete_tags_summary(file_path, original_name, external=False):
    """从文档中抽取文本，调用 DeepSeek 生成关键词和摘要。
    返回 {"tags": [...], "summary": "..."}，失败时抛异常。
    """
    text = _extract_text(file_path)
    context = text if text.strip() else f"文件名：{original_name}"
    prompt = (
        "你是金融研究报告助手。请根据下面的研究报告内容，提取关键词和摘要。"
        + ("同时识别报告原作者和发布机构；无法确认时返回空字符串。" if external else "")
        + "\n\n"
        "要求：\n"
        "1. 只输出一个JSON对象，键为 tags、summary、author、institution\n"
        "2. tags：3-6 个关键词，反映报告核心主题，用词简洁\n"
        "3. summary：不超过150字的中文摘要，概括报告的研究主题、主要内容或核心观点\n"
        "4. author 和 institution 仅填写文档明确出现的原作者/发布机构，不要把上传人当作作者\n"
        "5. 严格基于文档内容，不要编造\n\n"
        f"报告内容：\n{context}"
    )
    raw = _call_deepseek(prompt)
    data = json.loads(raw)
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [str(t).strip() for t in tags if str(t).strip()][:8]
    summary = str(data.get("summary", "")).strip()
    return {
        "tags": tags,
        "summary": summary,
        "author": str(data.get("author", "")).strip()[:100],
        "institution": str(data.get("institution", "")).strip()[:120],
    }


KNOWLEDGE_STOPWORDS = {
    "请问", "帮我", "告诉我", "什么", "如何", "哪些", "是否", "有没有", "最近", "近期",
    "目前", "当前", "相关", "报告", "研究", "分析", "观点", "情况", "主要", "内容",
    "影响", "原因", "变化", "方面", "关于", "以及", "可以", "一下", "今年", "最新",
    "主题", "完全", "存在", "不存在", "现在", "部门", "结论", "有什么", "报告",
    "报告么", "有么", "是否有", "里面", "这里", "其中",
}


def _knowledge_query_terms(question, vocabulary=""):
    """提取适合中文报告召回的主题词，降低“报告/研究/如何”等泛词的干扰。"""
    raw_terms = re.split(r"[\s,，。；;、？！?：:（）()]+", question)
    terms = {
        term.lower().strip() for term in raw_terms
        if len(term.strip()) >= 2 and term.strip() not in KNOWLEDGE_STOPWORDS
    }
    compact_query = re.sub(r"\s+", "", question).lower()
    subterms = set()
    for size in (2, 3, 4):
        for i in range(max(len(compact_query) - size + 1, 0)):
            piece = compact_query[i:i + size]
            if piece not in KNOWLEDGE_STOPWORDS and (not vocabulary or piece in vocabulary):
                subterms.add(piece)
    return compact_query, terms, subterms


def _knowledge_candidates(question, limit=6):
    """检索正文与元信息，过滤弱相关项，并返回带完整元数据的上下文候选。"""
    reports = store.reports()
    metadata_corpus = "".join(
        re.sub(r"\s+", "", " ".join([
            str(report.get("title", "")), str(report.get("summary", "")),
            str(report.get("recommendation", "")), " ".join(report.get("tags") or []),
            str(report.get("author", "")), str(report.get("theme", "")),
            REPORT_THEME_LABELS.get(str(report.get("theme", "")), ""),
            str(report.get("category", "")), REPORT_CATEGORY_LABELS.get(str(report.get("category", "")), ""),
        ])).lower()
        for report in reports
    )
    query, terms, subterms = _knowledge_query_terms(question, metadata_corpus)
    ranked = []
    for report in reports:
        title = str(report.get("title", ""))
        summary = str(report.get("summary", ""))
        recommendation = str(report.get("recommendation", ""))
        tags = " ".join(report.get("tags") or [])
        theme_key = str(report.get("theme", ""))
        category_key = str(report.get("category", ""))
        metadata = " ".join([title, summary, recommendation, tags, str(report.get("author", "")),
                             theme_key, REPORT_THEME_LABELS.get(theme_key, ""),
                             category_key, REPORT_CATEGORY_LABELS.get(category_key, "")])
        path = report_file_path(report)
        text = _extract_text(path) if path else ""
        fallback = "\n".join(filter(None, [recommendation, summary]))
        content = text.strip() or fallback
        metadata_compact = re.sub(r"\s+", "", metadata).lower()
        content_compact = re.sub(r"\s+", "", content).lower()
        full_compact = metadata_compact + content_compact

        score = 0
        if query and len(query) >= 3 and query in full_compact:
            score += 18
        for term in terms:
            if term in re.sub(r"\s+", "", title).lower():
                score += 14
            elif term in metadata_compact:
                score += 6
            if term in content_compact:
                score += 4
        for sub in subterms:
            if sub in metadata_compact:
                # 元信息中的 2~4 字主题词是高置信命中，例如“权益”“信用”“利差”。
                score += max(6, min(len(sub) * 2, 8))
            elif sub in content_compact:
                score += max(3, min(len(sub), 4))
        # 至少需要一个明确主题词/短语命中，避免仅凭“市场、表现”等泛词召回报告。
        if score < 6:
            continue

        published_at = str(report.get("reportDate") or report.get("uploadedAt", ""))
        ranked.append({
            "score": score,
            "published_at": published_at,
            "report": report,
            "content": content[:5500],
        })

    ranked.sort(key=lambda item: (item["score"], item["published_at"]), reverse=True)
    # 同标题报告只保留一份，优先保留相关度更高、发布时间更晚的版本。
    unique = []
    seen_titles = set()
    for item in ranked:
        title_key = re.sub(r"\s+", "", str(item["report"].get("title", "未命名报告"))).lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        unique.append(item)
        if len(unique) >= limit:
            break

    candidates = []
    for item in unique:
        report = item["report"]
        published_at = item["published_at"]
        candidates.append({
            "id": report["id"],
            "title": report.get("title", "未命名报告"),
            "author": report.get("author", ""),
            "published_at": published_at[:10] if published_at else "原文未提及",
            "theme": REPORT_THEME_LABELS.get(report.get("theme"), report.get("theme", "原文未提及")),
            "category": REPORT_CATEGORY_LABELS.get(report.get("category"), report.get("category", "原文未提及")),
            "content": item["content"],
        })
    return candidates


def _answer_knowledge_question(question):
    candidates = _knowledge_candidates(question)
    if not candidates:
        return {"answer": "未找到相关报告", "sources": []}
    ordered_candidates = sorted(candidates, key=lambda item: item["published_at"], reverse=True)
    material = "\n\n".join(
        f"[REPORT_ID:{item['id']}]\n"
        f"报告完整标题：{item['title']}\n"
        f"报告时间：{item['published_at']}\n"
        f"报告元数据方向：{item['theme']} / {item['category']}\n"
        f"报告作者：{item['author'] or '原文未提及'}\n"
        f"报告正文：\n{item['content']}"
        for item in ordered_candidates
    )
    system = (
        "你是一个专业的知识库报告检索与分析助手。你的核心任务是根据用户问题，"
        "从给定的【参考上下文】中精准筛选相关报告，并按指定结构输出摘要。\n\n"
        "严格规则：\n"
        "1. 仅使用【参考上下文】回答，禁止使用外部知识、常识补全或编造；上下文没有实质相关报告时，answer 必须严格为“未找到相关报告”。\n"
        "2. 客观中立，忠实还原报告原文观点，不添加个人评价、推测或总结性升华。\n"
        "3. 每篇报告必须包含发布时间、主要方向、主要观点；上下文缺失时对应字段写“原文未提及”。\n"
        "4. 合并重复报告，按发布时间倒序排列；只在 source_ids 中列出实际入选且支持答案的报告 ID。\n"
        "5. 逐一判断报告与问题是否实质相关，剔除仅关键词匹配但内容无关的报告。\n"
        "6. 每条主要观点精炼至 100 字以内，最多输出 3 条；不要输出上下文没有明确支持的数字、因果关系或结论。\n\n"
        "answer 必须严格使用以下模板，不要添加任何开场白、结束语或模板外内容：\n"
        "共检索到 {N} 篇相关报告：\n\n"
        "### 1. 《{报告完整标题}》\n"
        "- **发布时间**：{YYYY-MM-DD / YYYY年MM月}\n"
        "- **主要方向**：{用1句话概括报告研究的核心领域或主题}\n"
        "- **主要观点**：\n"
        "  - {核心观点1，精炼至100字以内}\n"
        "  - {核心观点2，精炼至100字以内}\n"
        "  - {核心观点3（如有），精炼至100字以内}\n\n"
        "依此类推。只输出一个 JSON 对象："
        "{\"answer\":\"严格按上述模板生成的字符串\",\"source_ids\":[\"报告ID\"]}。"
    )
    prompt = f"用户问题：{question}\n\n【参考上下文】\n{material}"
    raw = _call_deepseek(prompt, system=system, max_tokens=2800)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
    source_ids = data.get("source_ids", []) if isinstance(data, dict) else []
    if not isinstance(source_ids, list):
        source_ids = [source_ids]
    source_map = {item["id"]: item for item in candidates}
    sources = [
        {"id": rid, "title": source_map[rid]["title"], "author": source_map[rid]["author"],
         "publishedAt": source_map[rid]["published_at"]}
        for rid in source_ids if rid in source_map
    ]
    sources.sort(key=lambda item: item["publishedAt"], reverse=True)
    answer = str(data.get("answer", "")).strip() if isinstance(data, dict) else ""
    if not answer or answer == "报告库中暂无可用于回答的报告。":
        answer = "未找到相关报告"
    if answer == "未找到相关报告":
        sources = []
    return {"answer": answer, "sources": sources}


def json_error(message, code=400):
    return jsonify({"error": message}), code


# --------------------------------------------------------------------------- #
# 静态文件 & 入口路由
# --------------------------------------------------------------------------- #
def html_page(filename):
    html = Path(BASE_DIR, filename).read_text(encoding="utf-8")
    html = html.replace("__CSRF_TOKEN__", csrf_token())
    return Response(html, content_type="text/html; charset=utf-8")


@app.route("/")
def index():
    return html_page("index.html")


@app.route("/admin")
def admin_page():
    return html_page("admin.html")


@app.route("/admin.html")
def admin_html_page():
    """兼容通过 HTTP 直接访问 admin.html 的入口。"""
    return html_page("admin.html")


@app.route("/index.html")
def index_html_page():
    """兼容通过 HTTP 直接访问 index.html 的入口。"""
    return html_page("index.html")


@app.route("/<path:path>")
def static_files(path):
    # 仅开放前端静态资源目录，避免数据库、上传文件和源码被直接下载。
    normalized = path.replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    if not parts or parts[0] not in {"assets", "css", "js"} or any(part in {"", ".", ".."} for part in parts):
        return ("Not Found", 404)
    full_path = os.path.join(BASE_DIR, path)
    if os.path.isfile(full_path):
        return send_from_directory(BASE_DIR, path)
    return ("Not Found", 404)


# --------------------------------------------------------------------------- #
# 普通用户认证 API
# --------------------------------------------------------------------------- #
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    user = store.find_user_by_login(username)
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return json_error("账号或密码不正确", 401)
    session["internal_knowledge_base_user_id"] = user["id"]
    return jsonify({"user": public_user(user)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("internal_knowledge_base_user_id", None)
    return jsonify({"ok": True})


@app.route("/api/me", methods=["GET"])
def api_me():
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    return jsonify({"user": public_user(user)})


@app.route("/api/change-password", methods=["POST"])
def api_change_password():
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    data = request.get_json(silent=True) or {}
    old_pw = data.get("oldPassword", "")
    new_pw = data.get("newPassword", "")
    if not check_password_hash(user.get("password_hash", ""), old_pw):
        return json_error("当前密码不正确", 400)
    if len(new_pw) < 6:
        return json_error("新密码至少需要 6 位", 400)
    if new_pw == old_pw:
        return json_error("新密码不能与当前密码相同", 400)
    store.update_user(user["id"], {"password_hash": generate_password_hash(new_pw)})
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# 报告 API
# --------------------------------------------------------------------------- #
@app.route("/api/reports", methods=["GET"])
def api_reports_list():
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    reports = []
    for report in store.reports():
        item = public_report(report)
        item.update(store.report_engagement(report["id"], user["id"]))
        reports.append(item)
    return jsonify({"reports": reports})


@app.route("/api/report-authors", methods=["GET"])
def api_report_authors():
    """供行政角色上传报告时选择署名作者。"""
    user = require_role("admin")
    if not user:
        return json_error("仅行政角色可选择报告作者", 403)
    return jsonify({"authors": [public_user(item) for item in store.users()]})


@app.route("/api/reports", methods=["POST"], defaults={"report_scope": None})
@app.route("/api/reports/<report_scope>", methods=["POST"])
def api_reports_upload(report_scope=None):
    """单篇报告上传。FormData:
       - file: 单个文件
       - meta: JSON 字符串 {reportType, category, org, reportDate, summary,
               recommendation, tags, titles:{filename:title}, authorId}

       authorId 仅对行政角色生效；其他角色始终以当前登录用户作为作者。
    """
    user = require_user()
    if not user:
        return json_error("未登录", 401)

    files = request.files.getlist("file") or request.files.getlist("files")
    files = [f for f in files if f and f.filename]
    if not files:
        return json_error("请选择要上传的报告文件", 400)
    if len(files) > 1:
        return json_error("一次只能上传一篇报告", 400)

    meta_raw = request.form.get("meta", "{}")
    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        return json_error("元信息格式错误", 400)

    category = meta.get("category", "").strip()
    theme = meta.get("theme", "").strip()
    report_type = meta.get("reportType", "internal").strip()
    if report_scope not in (None, "internal", "external", "research_visit", "roadshow"):
        return json_error("报告上传入口无效", 404)
    if report_scope and report_type != report_scope:
        return json_error("该入口只能上传对应类型的报告", 400)
    org = meta.get("org", "").strip()
    report_date = meta.get("reportDate", "").strip()
    summary = meta.get("summary", "").strip()
    recommendation = meta.get("recommendation", "").strip()[:300]
    source_author = meta.get("sourceAuthor", "").strip()[:100]
    source_institution = meta.get("sourceInstitution", "").strip()[:120]
    tags_raw = meta.get("tags", "")
    titles = meta.get("titles", {}) or {}

    # 打分部门：仅月报/深度报告生效，默认两部门均打分
    scoring_orgs_raw = meta.get("scoringOrgs", [])
    if not isinstance(scoring_orgs_raw, list):
        scoring_orgs_raw = [scoring_orgs_raw]
    scoring_orgs = [org for org in scoring_orgs_raw if org in SCORING_ORGS]
    if report_type == "internal" and category in ("monthly", "deep"):
        if not scoring_orgs:
            return json_error("请至少选择一个打分部门", 400)
    else:
        scoring_orgs = list(SCORING_ORGS)

    author = user
    if user.get("role") == "admin":
        author_id = str(meta.get("authorId", "")).strip()
        if not author_id:
            return json_error("请选择报告作者", 400)
        author = store.get_user(author_id)
        if not author:
            return json_error("请选择有效的报告作者", 400)

    if report_type not in ("internal", "external", "research_visit", "roadshow"):
        return json_error("请选择有效的报告类型", 400)
    if report_type == "external":
        # 外部报告不使用内部报告分类字段，内部以 other 兼容旧数据结构保存。
        category = "other"
    elif report_type == "internal" and category not in ("weekly", "monthly", "deep", "other"):
        return json_error("请选择有效的报告分类", 400)
    if theme not in REPORT_THEMES:
        return json_error("请选择有效的报告主题", 400)
    if org not in ("资产配置部", "固收中心"):
        return json_error("请选择有效的所属部门", 400)
    if not report_date:
        return json_error("请选择报告日期", 400)
    if report_type == "external" and not source_author:
        return json_error("请填写外部报告作者", 400)
    if report_type == "external" and not source_institution:
        return json_error("请填写外部报告机构", 400)

    tags = [t.strip() for t in re.split(r"[,，]", tags_raw) if t.strip()][:8]

    created = []
    now_iso = _now_iso()
    for f in files:
        original = f.filename
        ext = os.path.splitext(original)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            continue
        # 读取文件内容检查大小
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_SIZE:
            return json_error(f"文件 {original} 超过 100MB 限制", 400)

        report_id = f"report-upload-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        save_name = stored_filename(report_id, original)
        save_path = os.path.join(UPLOAD_DIR, save_name)
        f.save(save_path)

        title = (titles.get(original) or "").strip() or os.path.splitext(original)[0]
        report = {
            "id": report_id,
            "title": title,
            "author": author["name"],
            "authorId": author["id"],
            "sourceAuthor": source_author if report_type == "external" else "",
            "sourceInstitution": source_institution if report_type == "external" else "",
            "org": org,
            "category": category,
            "theme": theme,
            "reportType": report_type,
            "reportDate": report_date,
            "uploadedAt": now_iso,
            "summary": summary,
            "recommendation": recommendation if report_type == "external" else "",
            "tags": tags,
            "fileName": original,
            "fileUrl": f"uploads/{save_name}",
            "fileType": ext.lstrip(".").upper() if ext else "FILE",
            "fileSize": format_bytes(size),
            "fileStored": True,
            "preset": False,
            "scoringOrgs": scoring_orgs,
        }
        store.add_report(report)
        created.append(report)

    if not created:
        return json_error("没有有效的文件被上传", 400)
    return jsonify({"reports": [public_report(r) for r in created], "count": len(created)})


@app.route("/api/reports/ai-complete", methods=["POST"])
def api_reports_ai_complete():
    """智能补全：接收单个文件，调用 DeepSeek 生成关键词和摘要。
    失败时返回空结果而非错误，不阻断上传流程。
    """
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    f = request.files.get("file")
    if not f or not f.filename:
        return json_error("请选择文件", 400)
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        return json_error("文件格式不支持", 400)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="ai_complete_")
    os.close(tmp_fd)
    try:
        f.save(tmp_path)
        external = str(request.form.get("reportType", "internal")).strip() == "external"
        result = _ai_complete_tags_summary(tmp_path, f.filename, external=external)
        return jsonify(result)
    except Exception as e:
        # 降级：返回空结果，前端提示手动填写
        return jsonify({"tags": [], "summary": "", "author": "", "institution": "", "error": f"智能补全失败：{e}"})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/api/reports/<rid>/file", methods=["GET"])
def api_report_file(rid):
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    rel = report.get("fileUrl", "")
    if not rel:
        return json_error("报告无文件", 404)
    full = report_file_path(report)
    if not full:
        return json_error("文件不存在", 404)
    return send_file(full, as_attachment=True, download_name=report.get("fileName") or os.path.basename(full))


@app.route("/api/reports/<rid>", methods=["DELETE"])
def api_report_delete(rid):
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    # 行政可删任意；普通用户仅删自己上传的
    if user.get("role") == "admin":
        pass
    elif report.get("authorId") == user.get("id"):
        pass
    else:
        return json_error("只能将自己上传的报告移入回收站", 403)
    store.trash_reports([rid], user.get("id"))
    return jsonify({"ok": True, "trashed": True})


@app.route("/api/reports/<rid>", methods=["PUT"])
def api_report_update(rid):
    """报告元信息更新：行政可改任意报告，其他角色仅改本人上传的报告。

    所有可编辑者可改：报告日期、摘要、关键词、内部报告二级分类。
    行政额外可改：报告作者（内部报告按 authorId 重置署名，外部报告改 sourceAuthor）。
    """
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    is_admin = user.get("role") == "admin"
    if not is_admin and report.get("authorId") != user.get("id"):
        return json_error("只能修改自己上传的报告", 403)

    data = request.get_json(silent=True) or {}
    fields = {}

    # 所有可编辑者可改的公共字段
    if "reportDate" in data:
        fields["reportDate"] = str(data["reportDate"]).strip()
    if "summary" in data:
        fields["summary"] = str(data["summary"] or "").strip()
    if "tags" in data:
        v = data["tags"]
        if isinstance(v, str):
            v = [t.strip() for t in re.split(r"[,，]", v) if t.strip()][:8]
        elif isinstance(v, list):
            v = [str(t).strip() for t in v if str(t).strip()][:8]
        else:
            v = []
        fields["tags"] = v
    # 研究主题：外部/调研/路演报告可在修改时调整（前端仅对非内部报告发送）
    if "theme" in data:
        theme = str(data["theme"]).strip()
        if theme not in REPORT_THEMES:
            return json_error("研究主题无效", 400)
        fields["theme"] = theme
    # 内部报告二级分类：周报/月报/深度报告/其他报告。改为月报/深度报告时，
    # 若未配置打分部门则默认两部门均打分（向后兼容旧报告）。
    if report.get("reportType") == "internal" and "category" in data:
        category = str(data["category"]).strip()
        if category not in ("weekly", "monthly", "deep", "other"):
            return json_error("分类无效", 400)
        fields["category"] = category
        if category in ("monthly", "deep"):
            existing = report.get("scoringOrgs")
            if not isinstance(existing, list) or not existing:
                fields["scoringOrgs"] = list(SCORING_ORGS)

    # 仅行政可改报告作者
    if is_admin:
        if report.get("reportType") == "external":
            if "sourceAuthor" in data:
                fields["sourceAuthor"] = str(data["sourceAuthor"] or "").strip()
        else:
            if "authorId" in data:
                author_id = str(data["authorId"]).strip()
                author = store.get_user(author_id)
                if not author:
                    return json_error("请选择有效的报告作者", 400)
                fields["author"] = author["name"]
                fields["authorId"] = author["id"]

    if not fields:
        return json_error("没有需要更新的字段", 400)
    updated = store.update_report(rid, fields)
    return jsonify({"ok": True, "report": public_report(updated)})


@app.route("/api/reports/<rid>/like", methods=["POST"])
def api_report_like(rid):
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    if report.get("reportType", "internal") == "internal":
        return json_error("内部报告不支持点赞", 400)
    return jsonify({"ok": True, **store.toggle_like(rid, user["id"])})


@app.route("/api/reports/<rid>/view", methods=["POST"])
def api_report_view(rid):
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    return jsonify({"ok": True, **store.add_view(rid, user["id"])})


@app.route("/api/reports/<rid>/favorite", methods=["POST"])
def api_report_favorite(rid):
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    if not store.get_report(rid):
        return json_error("报告不存在", 404)
    return jsonify({"ok": True, **store.toggle_favorite(rid, user["id"])})


@app.route("/api/work-reminders", methods=["GET"])
def api_work_reminders():
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    config = store.reminder_config()
    period = config.get("period") or datetime.now(CST).strftime("%Y")
    report_category = config.get("reportCategory", "deep")
    year_reports = [
        report for report in store.reports()
        if str(report.get("reportDate") or report.get("uploadedAt") or "")[:4] == period
    ]
    topic_reports = [report for report in year_reports
                     if report.get("reportType") == "internal" and report.get("category") == report_category]
    users = store.users()
    privileged = user.get("role") in ("leader", "admin")
    configured_rules = config.get("rules", [])
    if privileged:
        visible_rules = configured_rules
        visible_user_ids = {item["id"] for item in users}
    else:
        visible_rules = [rule for rule in configured_rules if user["id"] in (rule.get("userIds") or [])]
        visible_user_ids = {user["id"]}
        for rule in visible_rules:
            visible_user_ids.update(rule.get("userIds") or [])
    people = []
    for item in users:
        if item["id"] not in visible_user_ids:
            continue
        uploaded = [report for report in year_reports if report.get("authorId") == item["id"]]
        people.append({
            "id": item["id"], "name": item["name"], "org": item.get("org", ""),
            "count": len(uploaded),
            "reports": [{"id": report["id"], "title": report.get("title", ""),
                         "reportType": report.get("reportType", "internal"),
                         "category": report.get("category", "other")} for report in uploaded],
        })
    rules = []
    users_by_id = {item["id"]: item for item in users}
    for rule in visible_rules:
        member_ids = [uid for uid in rule.get("userIds", []) if uid in users_by_id]
        matched = [report for report in topic_reports if report.get("authorId") in member_ids]
        target = max(int(rule.get("target", 0) or 0), 0)
        rules.append({
            "id": rule.get("id", ""), "label": rule.get("label", "未命名要求"),
            "mode": rule.get("mode", "group"), "target": target,
            "completed": len(matched), "remaining": max(target - len(matched), 0),
            "members": [users_by_id[uid]["name"] for uid in member_ids],
            "reports": [{"id": report["id"], "title": report.get("title", "")} for report in matched],
        })
    return jsonify({
        "period": period, "reportCategory": report_category, "people": people, "rules": rules,
        "summary": {"target": sum(row["target"] for row in rules),
                    "completed": sum(min(row["completed"], row["target"]) for row in rules),
                    "remaining": sum(row["remaining"] for row in rules)},
    })


@app.route("/api/knowledge-search", methods=["GET", "POST", "DELETE"])
def api_knowledge_search():
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    if request.method == "DELETE":
        # 用户清空自己的知识搜索历史，不影响当日已用额度。
        store.clear_qa_history(user["id"])
        return jsonify({"ok": True})
    day = datetime.now(CST).strftime("%Y-%m-%d")
    used = store.qa_usage_today(user["id"], day)
    knowledge_config = store.knowledge_config()
    limit = knowledge_config["leaderLimit"] if user.get("role") == "leader" else knowledge_config["memberLimit"]
    if request.method == "GET":
        history = store.qa_history_for_user(user["id"])
        return jsonify({"limit": limit, "used": used, "remaining": max(limit - used, 0),
                        "available": bool(_llm_api_key()), "history": history})
    if used >= limit:
        return json_error(f"今日 {limit} 次知识搜索额度已用完，请明天再试", 429)
    question = str((request.get_json(silent=True) or {}).get("question", "")).strip()
    if len(question) < 2:
        return json_error("请输入具体问题", 400)
    if len(question) > 300:
        return json_error("问题请控制在 300 字以内", 400)
    if not _llm_api_key():
        return json_error("知识搜索尚未配置 DEEPSEEK_API_KEY", 503)
    try:
        result = _answer_knowledge_question(question)
    except Exception as exc:
        # 不把底层 WinError/代理地址直接暴露给用户，便于定位并保持提示可读。
        return json_error(f"知识搜索暂时不可用：{exc}", 503)
    store.add_qa_usage(user["id"], day, question)
    store.add_qa_history(user["id"], question, result.get("answer", ""), result.get("sources", []))
    return jsonify({**result, "limit": limit, "used": used + 1, "remaining": max(limit - used - 1, 0)})


def _remove_report_file(report):
    if not report.get("fileStored"):
        return
    rel = report.get("fileUrl", "")
    if not rel:
        return
    full = report_file_path(report)
    if full:
        try:
            os.remove(full)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 评分 API
# --------------------------------------------------------------------------- #
@app.route("/api/ratings", methods=["GET"])
def api_ratings_list():
    user = require_user()
    if not user:
        return json_error("未登录", 401)

    active_reports = [report for report in store.reports()
                      if report.get("reportType", "internal") == "internal"]
    active_report_ids = {report["id"] for report in active_reports}
    # 回收站报告的评分继续保存在数据库中，但前台暂不展示或计入统计；
    # 恢复报告后这些评分会原样重新出现。
    all_ratings = [rating for rating in store.ratings()
                   if rating.get("reportId") in active_report_ids]
    # 应评分人员总数随报告所选打分部门变化，因此按报告分别统计。
    scorer_ids_global = {u["id"] for u in store.users() if u.get("role") != "admin"}
    report_progress = {}
    for report in active_reports:
        if report.get("category") not in ("monthly", "deep"):
            continue
        eligible_ids = {u["id"] for u in eligible_scorers(report)}
        report_rows = [r for r in all_ratings if r.get("reportId") == report["id"]]
        done = len({r.get("userId") for r in report_rows if r.get("userId") in eligible_ids})
        total_scorers = len(eligible_ids)
        report_progress[report["id"]] = {
            "done": done,
            "pending": max(total_scorers - done, 0),
            "total": total_scorers,
        }

    # 报告作者可查看本人报告的团队总分及各维度均分。这里返回报告级总分
    # 和三维均分，但不下发任何评分人的记录；行政代上传时 authorId 已是被
    # 代传人，因而被代传人同样能获得本人报告的得分。
    authored_report_ids = {
        report["id"]
        for report in active_reports
        if report.get("authorId") == user.get("id")
        and report.get("category") in ("monthly", "deep")
    }
    report_scores = {}
    for report_id in authored_report_ids:
        report_rows = [r for r in all_ratings if r.get("reportId") == report_id]
        if not report_rows:
            report_scores[report_id] = {
                "overall": None,
                "inspiration": None,
                "depth": None,
                "utility": None,
            }
            continue
        overall = sum(
            (int(row["inspiration"]) + int(row["depth"]) + int(row["utility"])) / 3
            for row in report_rows
        ) / len(report_rows)
        dim_avg = lambda key: round(
            sum(int(row[key]) for row in report_rows) / len(report_rows), 1
        )
        report_scores[report_id] = {
            "overall": round(overall, 1),
            "inspiration": dim_avg("inspiration"),
            "depth": dim_avg("depth"),
            "utility": dim_avg("utility"),
        }

    is_leader = user.get("role") == "leader"
    if is_leader:
        users_by_id = {u["id"]: u for u in store.users()}
        visible_ratings = []
        for rating in all_ratings:
            item = dict(rating)
            scorer = users_by_id.get(rating.get("userId"), {})
            item["userName"] = scorer.get("name", "已删除用户")
            item["userOrg"] = scorer.get("org", "")
            visible_ratings.append(item)
        anonymous_feedback = []
    else:
        # 普通成员只需取回自己的评分以支持“已评/修改”；行政不取回任何具体分数。
        visible_ratings = ([r for r in all_ratings if r.get("userId") == user["id"]]
                           if user.get("role") != "admin" else [])
        anonymous_feedback = []
        for rating in all_ratings:
            if rating.get("userId") == user["id"] or not (rating.get("comment") or "").strip():
                continue
            anonymous_feedback.append({
                "reportId": rating.get("reportId"),
                "comment": rating.get("comment", ""),
                "updatedAt": rating.get("updatedAt"),
            })

    return jsonify({
        "ratings": visible_ratings,
        "feedback": anonymous_feedback,
        "reportProgress": report_progress,
        "reportScores": report_scores,
        "summary": {
            "totalRatings": len(all_ratings),
            "participants": len({r.get("userId") for r in all_ratings if r.get("userId") in scorer_ids_global}),
            "ratedReports": len({r.get("reportId") for r in all_ratings}),
            "totalScorers": len(scorer_ids_global),
        },
    })


@app.route("/api/ratings", methods=["POST"])
def api_rating_submit():
    user = require_user()
    if not user:
        return json_error("未登录", 401)
    if user.get("role") == "admin":
        return json_error("行政账号不参与评分", 403)

    data = request.get_json(silent=True) or {}
    report_id = data.get("reportId", "")
    report = store.get_report(report_id)
    if not report:
        return json_error("报告不存在", 404)
    if report.get("reportType", "internal") != "internal" or report.get("category") not in ("monthly", "deep"):
        return json_error("该报告无需评分", 400)
    # 仅所选打分部门的研究人员（及领导）具备评分资格；报告对所有人可见。
    if not is_eligible_scorer(report, user):
        return json_error("您不在此报告的打分人员范围内", 403)

    try:
        inspiration = int(data.get("inspiration"))
        depth = int(data.get("depth"))
        utility = int(data.get("utility"))
    except (TypeError, ValueError):
        return json_error("评分数据无效", 400)

    for v in (inspiration, depth, utility):
        if not (1 <= v <= 10):
            return json_error("评分需在 1-10 之间", 400)

    comment = (data.get("comment") or "").strip()[:500]
    if store.has_rating(report_id, user["id"]):
        return json_error("该报告您已提交过评分，评分提交后不可修改", 400)
    record = {
        "id": f"rating-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}",
        "reportId": report_id,
        "userId": user["id"],
        "inspiration": inspiration,
        "depth": depth,
        "utility": utility,
        "comment": comment,
        "updatedAt": _now_iso(),
    }
    store.add_rating(record)
    return jsonify({"ok": True, "rating": record})


@app.route("/api/reports/<rid>/scoring-status", methods=["GET"])
def api_scoring_status(rid):
    """查询某份报告的评分进度：哪些应评分人员已评/未评。
    仅行政与领导可访问（不泄露给普通成员）。
    """
    user = require_role("admin", "leader")
    if not user:
        return json_error("无权限", 403)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    if report.get("reportType", "internal") != "internal":
        return json_error("外部报告不参与评分", 400)
    # 应评分人员按报告所选打分部门确定（部门领导按 org 过滤，通用领导始终参与）
    scorers_all = eligible_scorers(report)
    ratings = [r for r in store.ratings() if r.get("reportId") == rid]
    rating_by_user = {r.get("userId"): r for r in ratings}
    scorers = []
    for u in scorers_all:
        r = rating_by_user.get(u["id"])
        scorers.append({
            "id": u["id"],
            "name": u["name"],
            "org": u["org"],
            "scored": r is not None,
            "updatedAt": r.get("updatedAt") if r else None,
            "score": (round((int(r["inspiration"]) + int(r["depth"]) + int(r["utility"])) / 3, 1)
                      if r else None),
        })
    done = sum(1 for s in scorers if s["scored"])
    response = {
        "reportId": rid,
        "total": len(scorers),
        "done": done,
        "pending": len(scorers) - done,
    }
    # 领导可查看具名评分进度和具体得分；行政可查看具名评分进度但不展示具体得分。
    if user.get("role") == "leader":
        response["scorers"] = scorers
    elif user.get("role") == "admin":
        response["scorers"] = [{**s, "score": None} for s in scorers]
    return jsonify(response)


# --------------------------------------------------------------------------- #
# 行政角色批量操作
# --------------------------------------------------------------------------- #
@app.route("/api/admin/batch-delete", methods=["POST"])
def api_batch_delete():
    user = require_role("admin")
    if not user:
        return json_error("无权限", 403)
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        return json_error("未选择报告", 400)
    trashed = store.trash_reports(ids, user.get("id"))
    return jsonify({"ok": True, "trashed": trashed})


@app.route("/api/admin/batch-category", methods=["POST"])
def api_batch_category():
    user = require_role("admin")
    if not user:
        return json_error("无权限", 403)
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    category = data.get("category", "")
    if not isinstance(ids, list) or not ids:
        return json_error("未选择报告", 400)
    if category not in ("weekly", "monthly", "deep", "other"):
        return json_error("分类无效", 400)
    for rid in ids:
        store.update_report(rid, {"category": category})
    return jsonify({"ok": True})


@app.route("/api/admin/batch-upload-time", methods=["POST"])
def api_batch_upload_time():
    user = require_role("admin")
    if not user:
        return json_error("无权限", 403)
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    uploaded_at = data.get("uploadedAt", "")
    if not isinstance(ids, list) or not ids:
        return json_error("未选择报告", 400)
    if not uploaded_at:
        return json_error("请提供上传时间", 400)
    # 允许 datetime-local 的 "YYYY-MM-DDTHH:MM" 格式
    for rid in ids:
        store.update_report(rid, {"uploadedAt": uploaded_at})
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# 预览转换 API
# --------------------------------------------------------------------------- #
@app.route("/api/preview", methods=["GET", "POST"])
def api_preview():
    if not require_user():
        return json_error("未登录", 401)
    if not SOFFICE:
        return json_error("服务器未安装 LibreOffice，无法转换文件", 500)

    work_dir = None
    try:
        if request.method == "GET":
            rel_path = request.args.get("file", "")
            if not rel_path:
                return json_error("缺少 file 参数", 400)
            normalized_rel = rel_path.replace("\\", "/").lstrip("/")
            report = next((item for item in store.reports()
                           if str(item.get("fileUrl", "")).replace("\\", "/").lstrip("/") == normalized_rel), None)
            if not report:
                return json_error("报告不存在或已在回收站", 404)
            file_path = report_file_path(report)
            if not file_path:
                return json_error("文件不存在", 404)

            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".pdf":
                return send_file(file_path, mimetype="application/pdf")
            if ext not in CONVERTIBLE_EXTS:
                return json_error(f"不支持的格式: {ext}", 400)

            # 永久缓存命中时直接复用。
            cached_pdf = _get_cached_pdf(file_path)
            if cached_pdf:
                return send_file(cached_pdf, mimetype="application/pdf")

            work_dir = _make_preview_work_dir()
            try:
                pdf_path = convert_to_pdf(file_path, work_dir)
                # 转换成功后落盘到持久缓存（失败不影响本次预览）
                cached_pdf = _store_cached_pdf(pdf_path, file_path, report.get("id"))
                if cached_pdf:
                    # 源 PDF 已 move 到缓存目录，直接发送缓存文件；清理临时工作目录
                    _remove_preview_artifacts(work_dir, retries=1, retry_delay=0)
                    return send_file(cached_pdf, mimetype="application/pdf")
                # 落盘失败则回退到一次性发送
                return _send_temporary_pdf(pdf_path, work_dir)
            except Exception:
                _remove_preview_artifacts(work_dir, retries=1, retry_delay=0)
                raise

        else:
            uploaded = request.files.get("file")
            if not uploaded:
                return json_error("缺少上传文件", 400)

            original_name = uploaded.filename or "upload"
            ext = os.path.splitext(original_name)[1].lower()
            if ext == ".pdf":
                return send_file(uploaded.stream, mimetype="application/pdf",
                                 as_attachment=False)
            if ext not in CONVERTIBLE_EXTS:
                return json_error(f"不支持的格式: {ext}", 400)

            work_dir = _make_preview_work_dir()
            tmp_path = os.path.join(work_dir, "source" + ext)
            uploaded.save(tmp_path)
            report_id = str(request.form.get("reportId", "")).strip() or None
            report = store.get_report(report_id) if report_id else None
            if report and report_file_path(report):
                source_path = report_file_path(report)
                cached_pdf = _get_cached_pdf(source_path)
                if cached_pdf:
                    _remove_preview_artifacts(work_dir, retries=1, retry_delay=0)
                    return send_file(cached_pdf, mimetype="application/pdf")
            pdf_path = convert_to_pdf(tmp_path, work_dir)
            if report and report_file_path(report):
                source_path = report_file_path(report)
                cached_pdf = _store_cached_pdf(pdf_path, source_path, report_id)
                if cached_pdf:
                    _remove_preview_artifacts(work_dir, retries=1, retry_delay=0)
                    return send_file(cached_pdf, mimetype="application/pdf")
            return _send_temporary_pdf(pdf_path, work_dir)

    except subprocess.TimeoutExpired:
        if work_dir:
            _schedule_preview_cleanup(work_dir)
        return json_error("转换超时，文件可能过大", 504)
    except Exception as e:
        if work_dir:
            _schedule_preview_cleanup(work_dir)
        return json_error(str(e), 500)


# --------------------------------------------------------------------------- #
# 超级管理员后台 API
# --------------------------------------------------------------------------- #
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    password_hash = store.admin_password_hash()
    if not password_hash:
        return json_error("后台管理员密码尚未初始化，请先执行账号迁移", 503)
    if not check_password_hash(password_hash, password):
        return json_error("后台密码不正确", 401)
    session["internal_knowledge_base_superadmin"] = True
    return jsonify({"ok": True})


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("internal_knowledge_base_superadmin", None)
    return jsonify({"ok": True})


@app.route("/api/admin/status", methods=["GET"])
def api_admin_status():
    return jsonify({"admin": bool(session.get("internal_knowledge_base_superadmin"))})


@app.route("/api/admin/pdf-cache", methods=["GET", "DELETE"])
def api_admin_pdf_cache():
    if not require_admin():
        return json_error("未登录后台", 401)
    if request.method == "DELETE":
        keys = store.clear_pdf_caches()
        deleted = _delete_cache_files(keys)
        return jsonify({"ok": True, "deleted": deleted})
    reports = {item["id"]: item for item in store.reports(include_deleted=True)}
    items = []
    total_size = 0
    for row in store.pdf_caches():
        report = reports.get(row.get("report_id")) or {}
        item = {
            "cacheKey": row["cache_key"], "reportId": row.get("report_id"),
            "reportTitle": report.get("title") or "—", "fileName": row["file_name"],
            "generatedAt": row["generated_at"], "lastAccessedAt": row["last_accessed_at"],
            "sizeBytes": row["size_bytes"], "conversionVersion": row["conversion_version"],
        }
        total_size += row["size_bytes"]
        items.append(item)
    return jsonify({"items": items, "count": len(items), "totalSizeBytes": total_size})


@app.route("/api/admin/pdf-cache/<cache_key>", methods=["DELETE"])
def api_admin_pdf_cache_delete(cache_key):
    if not require_admin():
        return json_error("未登录后台", 401)
    if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
        return json_error("缓存标识无效", 400)
    existed = store.delete_pdf_cache(cache_key)
    deleted = _delete_cache_files([cache_key])
    return jsonify({"ok": True, "deleted": bool(existed or deleted)})


@app.route("/api/admin/reports/<rid>/pdf-cache", methods=["DELETE"])
def api_admin_report_pdf_cache_delete(rid):
    if not require_admin():
        return json_error("未登录后台", 401)
    keys = store.delete_pdf_caches_for_report(rid)
    deleted = _delete_cache_files(keys)
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/admin/change-password", methods=["POST"])
def api_admin_change_password():
    if not require_admin():
        return json_error("未登录后台", 401)
    data = request.get_json(silent=True) or {}
    old_password = str(data.get("oldPassword", ""))
    new_password = str(data.get("newPassword", ""))
    confirm_password = str(data.get("confirmPassword", ""))
    current_hash = store.admin_password_hash() or ""
    if not check_password_hash(current_hash, old_password):
        return json_error("当前管理员密码不正确", 400)
    if len(new_password) < 8:
        return json_error("新密码至少需要8位", 400)
    if new_password != confirm_password:
        return json_error("两次输入的新密码不一致", 400)
    if new_password == old_password:
        return json_error("新密码不能与当前密码相同", 400)
    store.set_admin_password_hash(generate_password_hash(new_password))
    return jsonify({"ok": True})


@app.route("/api/admin/reports", methods=["GET"])
def api_admin_reports():
    if not require_admin():
        return json_error("未登录后台", 401)
    ratings = store.ratings()
    out = []
    for r in store.reports(include_deleted=True):
        item = public_report(r)
        item["ratingCount"] = sum(1 for x in ratings if x["reportId"] == r["id"])
        out.append(item)
    return jsonify({"reports": out})


@app.route("/api/admin/reports/<rid>", methods=["POST", "PUT"])
def api_admin_report_update(rid):
    if not require_admin():
        return json_error("未登录后台", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    data = request.get_json(silent=True) or {}
    allowed = ["title", "category", "theme", "reportType", "org", "reportDate",
               "uploadedAt", "summary", "recommendation", "tags", "author",
               "sourceAuthor", "sourceInstitution"]
    fields = {}
    for k in allowed:
        if k in data:
            v = data[k]
            if k == "category" and v not in ("weekly", "monthly", "deep", "other"):
                return json_error("分类无效", 400)
            if k == "theme" and v not in REPORT_THEMES:
                return json_error("主题无效", 400)
            if k == "reportType" and v not in ("internal", "external", "research_visit", "roadshow"):
                return json_error("报告类型无效", 400)
            if k == "org" and v not in ("资产配置部", "固收中心"):
                return json_error("部门无效", 400)
            if k == "tags":
                if isinstance(v, str):
                    v = [t.strip() for t in re.split(r"[,，]", v) if t.strip()][:8]
                elif isinstance(v, list):
                    v = [str(t).strip() for t in v if str(t).strip()][:8]
            fields[k] = v
    target_type = fields.get("reportType", report.get("reportType", "internal"))
    if target_type == "external":
        if not str(fields.get("sourceAuthor", report.get("sourceAuthor", ""))).strip():
            return json_error("外部报告必须填写原作者", 400)
        if not str(fields.get("sourceInstitution", report.get("sourceInstitution", ""))).strip():
            return json_error("外部报告必须填写机构", 400)
    updated = store.update_report(rid, fields)
    return jsonify({"ok": True, "report": public_report(updated)})


@app.route("/api/admin/reports/<rid>", methods=["DELETE"])
def api_admin_report_delete(rid):
    if not require_admin():
        return json_error("未登录后台", 401)
    report = store.get_report(rid, include_deleted=True)
    if not report:
        return json_error("报告不存在", 404)
    if not report.get("deletedAt"):
        return json_error("请先将报告移入回收站，再确认永久删除", 400)
    _remove_report_file(report)
    _delete_cache_files(store.delete_pdf_caches_for_report(rid))
    store.delete_reports([rid])
    return jsonify({"ok": True, "permanent": True})


@app.route("/api/admin/reports/<rid>/trash", methods=["POST"])
def api_admin_report_trash(rid):
    if not require_admin():
        return json_error("未登录后台", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在或已在回收站", 404)
    store.trash_reports([rid], "superadmin")
    return jsonify({"ok": True, "trashed": True})


@app.route("/api/admin/reports/<rid>/restore", methods=["POST"])
def api_admin_report_restore(rid):
    if not require_admin():
        return json_error("未登录后台", 401)
    report = store.get_report(rid, include_deleted=True)
    if not report:
        return json_error("报告不存在", 404)
    if not report.get("deletedAt"):
        return json_error("报告不在回收站", 400)
    restored = store.restore_report(rid)
    return jsonify({"ok": True, "report": public_report(restored)})


@app.route("/api/admin/reports/<rid>/reset-scores", methods=["POST"])
def api_admin_reset_scores(rid):
    if not require_admin():
        return json_error("未登录后台", 401)
    report = store.get_report(rid)
    if not report:
        return json_error("报告不存在", 404)
    store.reset_ratings_for_report(rid)
    return jsonify({"ok": True})


@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    if not require_admin():
        return json_error("未登录后台", 401)
    users = store.users()
    out = []
    for u in users:
        out.append({
            "id": u["id"], "name": u["name"], "org": u["org"], "role": u["role"],
            "defaultPassword": check_password_hash(u.get("password_hash", ""), DEFAULT_USER_PASSWORD),
        })
    return jsonify({"users": out})


@app.route("/api/admin/users", methods=["POST"])
def api_admin_user_create():
    if not require_admin():
        return json_error("未登录后台", 401)
    data = request.get_json(silent=True) or {}
    uid = (data.get("id") or "").strip().lower().replace(" ", "")
    name = (data.get("name") or "").strip()
    org = (data.get("org") or "").strip()
    role = (data.get("role") or "").strip()
    password = (data.get("password") or "").strip() or DEFAULT_USER_PASSWORD
    if not uid or not name:
        return json_error("账号和姓名必填", 400)
    if not re.match(r"^[a-z0-9_.-]+$", uid):
        return json_error("账号只能包含小写字母、数字及 _ . -", 400)
    if org not in ("资产配置部", "固收中心", "领导", "行政", ""):
        return json_error("部门无效", 400)
    if role not in ("leader", "admin", "member"):
        return json_error("角色无效", 400)
    if store.get_user(uid):
        return json_error("账号已存在", 400)
    user = {"id": uid, "name": name, "org": org, "role": role,
            "password_hash": generate_password_hash(password)}
    store.add_user(user)
    return jsonify({"ok": True, "user": public_user(user)})


@app.route("/api/admin/users/<uid>", methods=["POST", "PUT"])
def api_admin_user_update(uid):
    if not require_admin():
        return json_error("未登录后台", 401)
    user = store.get_user(uid)
    if not user:
        return json_error("用户不存在", 404)
    data = request.get_json(silent=True) or {}
    fields = {}
    for k in ("name", "org", "role"):
        if k in data:
            v = (data[k] or "").strip() if isinstance(data[k], str) else data[k]
            if k == "org" and v not in ("资产配置部", "固收中心", "领导", "行政", ""):
                return json_error("部门无效", 400)
            if k == "role" and v not in ("leader", "admin", "member"):
                return json_error("角色无效", 400)
            if k == "name" and not v:
                return json_error("姓名不能为空", 400)
            fields[k] = v
    updated = store.update_user(uid, fields)
    return jsonify({"ok": True, "user": public_user(updated)})


@app.route("/api/admin/users/<uid>", methods=["DELETE"])
def api_admin_user_delete(uid):
    if not require_admin():
        return json_error("未登录后台", 401)
    if not store.get_user(uid):
        return json_error("用户不存在", 404)
    store.delete_user(uid)
    return jsonify({"ok": True})


@app.route("/api/admin/users/<uid>/reset-password", methods=["POST"])
def api_admin_user_reset_password(uid):
    if not require_admin():
        return json_error("未登录后台", 401)
    user = store.get_user(uid)
    if not user:
        return json_error("用户不存在", 404)
    data = request.get_json(silent=True) or {}
    password = (data.get("password") or "").strip()
    if not password:
        return json_error("请输入新密码", 400)
    if len(password) < 6:
        return json_error("密码至少 6 位", 400)
    store.update_user(uid, {"password_hash": generate_password_hash(password)})
    return jsonify({"ok": True})


@app.route("/api/admin/users/<uid>/clear-qa-history", methods=["POST"])
def api_admin_user_clear_qa_history(uid):
    if not require_admin():
        return json_error("未登录后台", 401)
    if not store.get_user(uid):
        return json_error("用户不存在", 404)
    removed = store.clear_qa_history(uid)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/admin/reminder-config", methods=["GET", "PUT", "POST"])
def api_admin_reminder_config():
    if not require_admin():
        return json_error("未登录后台", 401)
    if request.method == "GET":
        return jsonify({"config": store.reminder_config()})
    data = request.get_json(silent=True) or {}
    period = str(data.get("period", "")).strip()
    if not re.match(r"^\d{4}$", period):
        return json_error("统计年度格式应为 YYYY", 400)
    category = str(data.get("reportCategory", "deep")).strip()
    if category not in ("weekly", "monthly", "deep", "other"):
        return json_error("专题报告分类无效", 400)
    valid_user_ids = {item["id"] for item in store.users()}
    clean_rules = []
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return json_error("规则格式无效", 400)
    for index, rule in enumerate(rules[:100]):
        if not isinstance(rule, dict):
            continue
        label = str(rule.get("label", "")).strip()[:80]
        if not label:
            return json_error(f"第 {index + 1} 条规则缺少名称", 400)
        try:
            target = int(rule.get("target", 0))
        except (TypeError, ValueError):
            return json_error(f"第 {index + 1} 条规则目标数量无效", 400)
        if not 0 <= target <= 100:
            return json_error("目标数量应在 0-100 之间", 400)
        user_ids = list(dict.fromkeys(
            str(uid) for uid in (rule.get("userIds") or []) if str(uid) in valid_user_ids
        ))
        clean_rules.append({
            "id": str(rule.get("id") or f"rule-{uuid.uuid4().hex[:8]}"),
            "label": label, "mode": "person" if rule.get("mode") == "person" else "group",
            "target": target, "userIds": user_ids,
        })
    config = {"period": period, "reportCategory": category, "rules": clean_rules}
    store.set_reminder_config(config)
    return jsonify({"ok": True, "config": config})


@app.route("/api/admin/knowledge-config", methods=["GET", "PUT", "POST"])
def api_admin_knowledge_config():
    if not require_admin():
        return json_error("未登录后台", 401)
    if request.method == "GET":
        return jsonify({"config": store.knowledge_config()})
    data = request.get_json(silent=True) or {}
    try:
        member_limit = int(data.get("memberLimit", 10))
        leader_limit = int(data.get("leaderLimit", 100))
    except (TypeError, ValueError):
        return json_error("知识搜索额度必须是整数", 400)
    if not 1 <= member_limit <= 1000 or not 1 <= leader_limit <= 1000:
        return json_error("知识搜索额度应在 1 至 1000 次之间", 400)
    config = {"memberLimit": member_limit, "leaderLimit": leader_limit}
    store.set_knowledge_config(config)
    return jsonify({"ok": True, "config": config})


@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    if not require_admin():
        return json_error("未登录后台", 401)
    reports = store.reports()
    all_reports = store.reports(include_deleted=True)
    users = store.users()
    active_ids = {report["id"] for report in reports}
    ratings = [rating for rating in store.ratings() if rating.get("reportId") in active_ids]
    return jsonify({
        "reports": len(reports),
        "internalReports": sum(1 for report in reports if report.get("reportType", "internal") == "internal"),
        "externalReports": sum(1 for report in reports if report.get("reportType") == "external"),
        "deletedReports": sum(1 for report in all_reports if report.get("deletedAt")),
        "users": len(users),
        "ratings": len(ratings),
        "ratedReports": len({r["reportId"] for r in ratings}),
        "participants": len({r["userId"] for r in ratings}),
        "byCategory": {c: sum(1 for r in reports if r.get("category") == c)
                       for c in ("weekly", "monthly", "deep", "other")},
        "byTheme": {t: sum(1 for r in reports if r.get("theme") == t)
                    for t in REPORT_THEMES},
    })
