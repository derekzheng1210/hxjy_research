# -*- coding: utf-8 -*-
"""债券详查——发行人信用研究 AI Agent。

链路（对应需求文档《债券详查发行人信用研究需求文档》V1.0）：
1. 按债券代码组装聚源数据快照（Oracle 实时字段 + 本地静态缓存 + 门户确定性诊断）；
2. 将固定最高优先级指令 + 预设任务 + 快照提交公司 Agent（iam.hxjyam.com supaw
   流式接口，自动登录、token 过期自愈），Agent 未配置或调用失败时回退到
   llm_config 注册的本地大模型链（仅基于快照分析，不再检索）；
3. 流式解析阶段事件：思考过程（reasoning）与中间消息一律丢弃，不进入结果；
4. 抽取 JSON、结构校验与归一化（超3条的变化/风险移入 details，坏引用剔除），
   校验失败先用本地模型做一次"仅修复格式"重排，仍失败则任务失败；
5. 结果按发行人落盘缓存（reports/*.json），任务状态落盘（jobs/*.job.json），
   兼容 gunicorn 多 worker 与重启；刷新失败时保留上一版成功报告。

前端仅展示阶段进度条（排队中/查询聚源数据/检索补充资料/生成结论/结构校验/完成），
不展示思考过程。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from llm_config import _env_or_registry
from paths import DATA_DIR

# --------------------------------------------------------------------------- #
# 配置：公司 Agent（supaw）连接参数，环境变量优先，Windows 回退读 HKCU\Environment
# --------------------------------------------------------------------------- #
AGENT_BASE_URL = os.environ.get("IAM_AGENT_BASE_URL", "https://iam.hxjyam.com").rstrip("/")
AES_KEY = "ruijiancloudbase"  # 官方前端同款：AES-128-CFB，key=IV，NoPadding
AGENT_BASIC = base64.b64encode(b"rui:rui").decode()
AGENT_TENANT_ID = "1"
REQUEST_TIMEOUT = (15, 120)  # 连接、单次读块超时
STREAM_MAX_SECONDS = 600  # 单次流式调用总时长看门狗：上游 keepalive 会让读超时失效
JOB_STALE_SECONDS = 15 * 60  # 任务文件超过该时长无更新视为僵死，可被接管
REPORT_TTL_DAYS = 7  # 经营边际变化/主要风险建议有效期；过期仍展示但标记 stale

RESEARCH_DIR = DATA_DIR / "credit_research"
JOBS_DIR = RESEARCH_DIR / "jobs"
REPORTS_DIR = RESEARCH_DIR / "reports"

STAGE_QUEUED = "queued"
STAGE_JUYUAN = "juyuan"
STAGE_AGENT = "agent"
STAGE_CONCLUDE = "conclude"
STAGE_VALIDATE = "validate"
STAGE_DONE = "done"
STAGE_FAILED = "failed"
STAGE_TEXT = {
    STAGE_QUEUED: "排队中",
    STAGE_JUYUAN: "查询聚源数据",
    STAGE_AGENT: "检索补充资料",
    STAGE_CONCLUDE: "生成结论",
    STAGE_VALIDATE: "结构校验",
    STAGE_DONE: "完成",
    STAGE_FAILED: "失败",
}

CLASSIFICATION_TYPES = ("CITY_PLATFORM", "CITY_HYBRID", "NON_CITY", "UNCERTAIN")
SECTION_MODES = ("full", "profile", "changes", "risks", "opinion")
SECTION_LABELS = {"full": "完整研究", "profile": "公司介绍", "changes": "边际变化",
                  "risks": "风险", "opinion": "舆情"}
CLASSIFICATION_LABELS = {
    "CITY_PLATFORM": "城投",
    "CITY_HYBRID": "城投属性/混合性质",
    "NON_CITY": "非城投",
    "UNCERTAIN": "待确认",
}
DIRECTION_LABELS = {"positive": "改善", "negative": "恶化", "neutral": "持平", "uncertain": "待观察"}
SUBTYPE_OPTIONS = [
    "传统城投平台", "园区开发/产城运营", "公用事业", "交通基础设施", "产业投资集团",
    "国有资本运营公司", "一般产业企业", "房地产", "银行", "券商", "保险",
    "租赁/其他金融", "其他",
]

_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}
# 任务启动互斥：防止并发请求对同一发行人重复起线程（查运行中→建任务→起线程
# 必须原子完成）；跨进程（gunicorn 多 worker）由任务文件的存在性兜底
_start_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Agent 客户端
# --------------------------------------------------------------------------- #
def agent_configured() -> bool:
    return bool(_env_or_registry("IAM_AGENT_USERNAME").strip() and _env_or_registry("IAM_AGENT_PASSWORD").strip())


def agent_connection_overview() -> dict[str, Any]:
    """Return non-sensitive Agent connection metadata for the admin page."""
    return {
        "configured": agent_configured(),
        "base_url": AGENT_BASE_URL,
        "key_hint": "IAM_AGENT_USERNAME / IAM_AGENT_PASSWORD",
    }


def test_agent_connection() -> dict[str, Any]:
    """Verify that the configured Agent credentials can obtain a fresh token.

    This deliberately stops at authentication: it validates DNS/TLS, the Agent
    login endpoint, encryption and credentials without creating a research job
    or consuming a chat request.
    """
    started = time.perf_counter()
    overview = agent_connection_overview()
    if not overview["configured"]:
        return {**overview, "ok": False, "error": "未配置 AI Agent 凭证", "latency_ms": None}
    try:
        get_agent_token(force=True)
        return {**overview, "ok": True, "latency_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return {**overview, "ok": False, "error": str(exc)[:200], "latency_ms": round((time.perf_counter() - started) * 1000)}


def _agent_credentials() -> tuple[str, str]:
    return (
        _env_or_registry("IAM_AGENT_USERNAME").strip(),
        _env_or_registry("IAM_AGENT_PASSWORD").strip(),
    )


def _encrypt_password(plain: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

    try:  # cryptography>=43 将 CFB 移入 decrepit，功能等价
        from cryptography.hazmat.decrepit.ciphers.modes import CFB as _cfb_mode
    except ImportError:  # pragma: no cover
        from cryptography.hazmat.primitives.ciphers.modes import CFB as _cfb_mode

    key = AES_KEY.encode("utf-8")
    encryptor = Cipher(algorithms.AES(key), _cfb_mode(key)).encryptor()
    return base64.b64encode(encryptor.update(plain.encode("utf-8")) + encryptor.finalize()).decode()


def _login(username: str, password: str) -> tuple[str, float]:
    from urllib.parse import quote, urlencode

    qs = f"?username={quote(username)}&grant_type=password&scope=server"
    req = urllib.request.Request(
        AGENT_BASE_URL + "/api/auth/oauth2/token" + qs,
        data=urlencode({"password": _encrypt_password(password)}).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {AGENT_BASIC}",
            "TENANT-ID": AGENT_TENANT_ID,
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=REQUEST_TIMEOUT[0]) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != "00000" or not (payload.get("data") or {}).get("accessToken", {}).get("tokenValue"):
        raise RuntimeError(f"Agent登录失败: {str(payload.get('msg') or payload)[:160]}")
    access = payload["data"]["accessToken"]
    expires_at = _parse_expires(access.get("expiresAt"))
    return access["tokenValue"], expires_at


def _parse_expires(value: Any) -> float:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return time.time() + 3600
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.timestamp()


def get_agent_token(force: bool = False) -> str:
    username, password = _agent_credentials()
    if not username or not password:
        raise RuntimeError("AI Agent未配置（IAM_AGENT_USERNAME / IAM_AGENT_PASSWORD）")
    with _token_lock:
        fresh = _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60
        if not fresh or force:
            token, expires_at = _login(username, password)
            _token_cache.update(token=token, expires_at=expires_at)
        return _token_cache["token"]


def _agent_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False  # 与 llm_config 一致：内网接口不走系统代理
    return session


def _iter_sse_data(resp) -> Any:
    """逐行解析 SSE，yield 每条 data 负载（字符串）。"""
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line.startswith("data:"):
            yield line[5:].strip()


def _run_agent_stream(prompt: str, on_event: Callable[[str, dict], None]) -> tuple[str, dict]:
    """调用公司 Agent 流式接口，返回 (最终正文, 元信息)。

    思考过程（reasoning 消息）与中间 message（如"已获取核心财务…"、泄露的英文
    自言自语）不属于交付正文：只采纳最后一条 assistant message 的文本；
    其余消息的流式增量仅用于进度统计。401/424 时强制重登一次并重放。
    """
    body = {
        "input": [{"role": "user", "type": "message",
                   "content": [{"type": "text", "text": prompt, "status": "created"}]}],
        "session_id": str(int(time.time() * 1000)),
        "user_id": _agent_credentials()[0],
        "channel": "console",
        "stream": True,
    }
    session = _agent_session()

    def post() -> requests.Response:
        return session.post(
            AGENT_BASE_URL + "/api/aipa/v1/supaw/chat/stream",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Authorization": f"Bearer {get_agent_token()}",
            },
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )

    resp = post()
    if resp.status_code in (401, 424):
        resp.close()
        get_agent_token(force=True)
        resp = post()
    if resp.status_code >= 400:
        detail = resp.text[:200]
        resp.close()
        raise RuntimeError(f"Agent接口返回HTTP {resp.status_code}: {detail}")

    plugin_calls = 0
    last_plugin = ""
    answer_chars = 0
    usage: dict = {}
    reasoning_ids: set[str] = set()
    message_order: list[str] = []
    completed_message_ids: set[str] = set()
    text_by_msg: dict[str, str] = {}
    stream_started = time.time()
    try:
        for payload in _iter_sse_data(resp):
            if time.time() - stream_started > STREAM_MAX_SECONDS:
                raise RuntimeError(f"Agent流式响应超过{STREAM_MAX_SECONDS}秒未完成，已中止")
            if payload == "complete":
                break
            try:
                outer = json.loads(payload)
            except ValueError:
                continue
            if outer.get("type") == "complete":
                break
            try:
                inner = json.loads(outer.get("data") or "{}")
            except (ValueError, TypeError):
                continue
            kind = inner.get("type")
            status = inner.get("status")
            if kind == "reasoning":
                if inner.get("id"):
                    reasoning_ids.add(inner["id"])
                on_event("reasoning", {})
            elif kind == "message":
                if inner.get("id"):
                    if inner["id"] not in message_order:
                        message_order.append(inner["id"])
                    if status == "completed":
                        completed_message_ids.add(inner["id"])
            elif kind == "plugin_call" and status == "completed":
                plugin_calls += 1
                on_event("plugin", {"count": plugin_calls, "name": last_plugin})
            elif kind == "data" and isinstance(inner.get("data"), dict) and inner["data"].get("name"):
                last_plugin = str(inner["data"]["name"])
            elif kind == "turn_usage":
                usage = inner.get("usage") or {}
            elif kind == "text" and inner.get("delta") and inner.get("text") and inner.get("msg_id"):
                msg_id = str(inner["msg_id"])
                text_by_msg[msg_id] = text_by_msg.get(msg_id, "") + inner["text"]
                if msg_id not in reasoning_ids:
                    answer_chars += len(inner["text"])
                    on_event("answer_delta", {"chars": answer_chars})
    finally:
        resp.close()

    # 正文 = 最后一条 assistant message；优先采用已 completed 的，其次流式累计文本
    answer = ""
    for msg_id in reversed(message_order):
        candidate = text_by_msg.get(msg_id, "")
        if not candidate:
            continue
        if msg_id in completed_message_ids or len(message_order) == 1:
            answer = candidate
            break
        # 未 completed 的中间消息可能是过程稿：仅在没有更优候选时采用
        if not answer:
            answer = candidate
    if not answer:  # 兜底：拼接全部非 reasoning 文本
        answer = "\n".join(
            text for msg_id, text in text_by_msg.items() if msg_id not in reasoning_ids
        ).strip()
    meta = {
        "plugin_calls": plugin_calls,
        "model": usage.get("model_name") or "",
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "channel": "company_agent",
    }
    if not answer.strip():
        raise RuntimeError("Agent未返回正文")
    return answer, meta


def _local_llm_available() -> bool:
    from llm_config import available_providers

    return bool(available_providers())


def _run_local_llm(prompt: str, on_event: Callable[[str, dict], None]) -> tuple[str, dict]:
    """回退链：llm_config 注册的 OpenAI 兼容模型（无检索工具，仅基于快照分析）。"""
    from llm_config import available_providers

    providers = available_providers()
    if not providers:
        raise RuntimeError("未配置任何大模型")
    messages = [
        {"role": "system", "content": prompt.split("\n\n===任务===\n\n")[0]},
        {"role": "user", "content": prompt.split("\n\n===任务===\n\n", 1)[1] if "\n\n===任务===\n\n" in prompt else prompt},
    ]
    last_error: Exception | None = None
    for provider in providers:
        payload = {
            "model": provider["model"], "messages": messages, "temperature": 0.2,
        }
        if provider.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        if provider.get("disable_thinking"):
            payload["thinking"] = {"type": "disabled"}
        if provider.get("chat_template_kwargs"):
            payload["chat_template_kwargs"] = provider["chat_template_kwargs"]
        req = urllib.request.Request(
            provider["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {provider['api_key']}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            on_event("reasoning", {})
            with opener.open(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data["choices"][0]["message"] or {}).get("content") or ""
            if not content.strip():
                raise RuntimeError("模型未返回正文")
            return content, {"plugin_calls": 0, "model": provider["model"], "channel": "local_llm"}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:160]
            last_error = RuntimeError(f"{provider['name']} HTTP {exc.code}: {detail}")
        except Exception as exc:  # noqa: BLE001 - 逐个 provider 兜底
            last_error = exc
    raise RuntimeError(f"本地大模型调用失败：{last_error}")


def run_research_prompt(prompt: str, on_event: Callable[[str, dict], None]) -> tuple[str, dict]:
    """优先公司 Agent（带检索），未配置或失败时回退本地大模型链。"""
    if agent_configured():
        try:
            return _run_agent_stream(prompt, on_event)
        except Exception as exc:  # noqa: BLE001
            on_event("agent_error", {"message": str(exc)[:200]})
            if not _local_llm_available():
                raise
    return _run_local_llm(prompt, on_event)


# --------------------------------------------------------------------------- #
# 提示词（需求文档 §9 固定指令 + §10 预设任务 + §11 输出结构）
# --------------------------------------------------------------------------- #
SYSTEM_INSTRUCTIONS = """你是一名资深信用债发行人研究员。目标：生成短、准、可追溯的发行人信用研究，供投资经理快速掌握主体信用状况。不是长篇公司介绍，不是股票报告，不是通用风险清单。

一、聚源数据优先，快照只是起点
1. 系统已在任务末尾提供"聚源数据快照"（含查询日期），它是主体识别与债券事实的起点与校验基准。你必须继续使用可用的聚源数据查询工具，按下方"必查清单"补齐财务、存续债、评级与区域数据，不能因为快照里没有就把字段写进data_gaps了事。
2. 每个关键数值都必须有报告期和单位；预测值不得写成实际值。
3. 快照与查询结果冲突时，核对报告期、单位、合并范围后说明采用口径。
4. 严禁张冠李戴：所有公司、区域、集团名称必须与发行人严格一致，不得混入其他主体的事实。

二、必查清单（用数据查询工具逐项完成，查不到的才写入data_gaps）
1. 财务（所有主体）：最近三年年报+最新一期（如最新半年报）的营业收入、净利润、货币资金、有息债务、短期有息债务、经营活动现金流净额、资产负债率、对外担保；计算同比与趋势方向。
2. 债务（所有主体）：存续债券明细（余额、票面利率、到期/行权日）与未来1年、1—3年到期分布；最新主体评级、债项评级、中债隐含评级及调整记录。
3. 城投及城投属性主体加查：所在区域GDP、一般公共预算收入、政府性基金收入、地方政府债务余额与债务率、土地出让收入。逐年分别查询最近三个完整年度（例如2023、2024、2025，当前已进入2026年时最新完整年度为2025年）并全部列出，不得只给一两个年度；一次查询只返回单年数据时逐年重复查询。另查：发行人在当地平台体系中的层级与地位；政府应收款、财政补贴与资产注入情况。
4. 产业类主体加查：行业供需与价格趋势、公司竞争地位与市场占有率、资本开支与投资回收。行业属性鲜明的主体（如白酒、地产、煤炭、钢铁、水泥、航空、化工等）必须查询该行业的关键景气数据——例如产品批价/价格指数、行业产量或销量、库存、龙头业绩对比等——取最近三年或可得的最近三期，逐期写入juyuan_metrics（category="行业数据"）。
5. 舆情（检索工具）：按"发行人及核心子公司 → 同城/同集团其他平台 → 区域与行业"三个层次核查。

三、先分类，再选择分析框架
1. 综合聚源城投标志、企业性质、实控人、主营业务、平台职能、政府应收款与市场化经营情况，分类为：CITY_PLATFORM（城投）、CITY_HYBRID（城投属性/混合性质）、NON_CITY（非城投）、UNCERTAIN（待确认）。
2. 不得仅凭单一城投标志下结论；给出2-5条最关键依据（用业务语言，如"聚源城投标志为是""实控人为邯郸市国资委"）、冲突证据和置信度。
3. CITY_PLATFORM：分析区域财政、平台地位、政府支持和再融资，不写泛化行业分析。
4. CITY_HYBRID：双线分析政府/平台属性与市场化经营，并明确回答信用基础更依赖哪一侧。
5. NON_CITY：分析行业、商业模式、经营现金流、杠杆和融资能力。
6. 证据冲突无法可靠判断时返回UNCERTAIN，不得强制归类。

四、边际变化要能支撑投资决策
1. 每条变化必须基于多期比较（近三年+最新一期），写清事实、比较期间、具体数值与幅度（如"货币资金由2023年末29.94亿元降至2024年末13.71亿元，腰斩"）、原因与信用含义。
2. 优先选取对偿债能力有实质影响的维度：收入与盈利、经营现金流、货币资金与短期债务覆盖、债务结构与到期压力、对外担保、区域财政（城投）。
3. direction标记positive/negative/neutral/uncertain；静态数值不得表述为改善或恶化；单一无关紧要的事实不得充当边际变化。

五、风险要具体、可跟踪
1. 每项风险必须包含发行人特定证据（数字或事件）、信用传导路径、severity与monitor监测指标。
2. 不得输出"宏观下行""行业竞争"等无主体证据的套话；资料只支持1项就只输出1项。

六、重大舆情：以负面事件为核心，三层核查
1. 发行人直接负面舆情（决定brief.public_opinion.verdict）：只统计与发行人、实控人、控股股东或重要核心子公司直接相关的重大负面/风险类事件——违约或欠息、票据逾期、被执行或限高、重大诉讼、评级下调、监管处罚、高管被查、经营恶化被媒体报道等。verdict三选一：found（发现此类负面事件）/ not_found（未发现）/ insufficient（资料不足或检索失败）；检索失败不得返回not_found。
2. 正常发债、中标利率、ABS获批、评级维持、常规业绩披露等中性或正面事件不算舆情发现，不得作为found依据；如期间内存在此类事件，可在conclusion中一句话带过作为背景（如"期间内有正常发行与ABS获批，融资渠道顺畅"）。
3. 区域与关联平台舆情必须检索并逐条记入details.public_opinion_events（level="区域"或"关联平台"）：同城其他城投平台、同集团平台的负面信用事件——被列入票据交所披露名单、被执行/限高、亏损、债务逾期、高管频繁变动等。这是区域信用环境的重要信号，不得遗漏。
4. 行业与政策：影响该类主体再融资环境的政策与行业负面事件，记入public_opinion_events（level="行业"）。
5. public_opinion_events只收录负面/风险类事件；中性或正面事件（发债、中标、ABS获批、评级维持、常规业绩披露等）一律不写入该数组。每条事件包含：date、level（发行人/区域/关联平台/行业）、title、summary、credit_impact、source_name；summary写事实，credit_impact写对发行人偿债环境的影响；查无负面事件则返回空数组。

七、details必须详实（不受摘要字数限制，必填）
details中的数据表是交付物的一部分：juyuan_metrics少于12条、城投主体的city_analysis为空、产业主体的industry_analysis为空，均视为不合格输出；宁可压缩每条文字表述，也不得删减必填数据行。
1. company_profile：实控人、股权层级、核心业务、区域与集团定位、资产规模。
2. bond_profile：债券基本情况——发行规模与当前余额、票面利率、期限与还本方式、评级、担保人、主承销商（以查询结果为准，查不到的省略）。
3. city_analysis：区域经济财政数据表（键值对注明年份，如"政府性基金收入(2024)：105.8亿元，同比-48.5%"）、平台层级与地位、存续债与到期结构、政府支持情况。
4. hybrid_analysis：政府线与市场线分块，并回答四个问题：信用基础更依赖哪侧、市场化转型是否真改善现金流、政府支持弱化时自身能否担债、市场化波动是否反噬平台信用。
5. industry_analysis：行业供需、价格、竞争格局与公司位置。
6. juyuan_metrics：每条含category、metric、value、unit、period。category四选一："主体财务"（营收、净利润、货币资金、有息债务、短期有息债务、经营现金流净额、资产负债率、对外担保等）、"区域财政"（城投及城投属性主体必填：GDP、一般公共预算收入、政府性基金收入、地方政府债务余额、债务率、土地出让收入等，至少最近三个年度，以可得的最新完整年度为终点）、"行业数据"（产业类主体必填：该行业关键景气指标，如产品价格/批价、行业产量销量、库存、价格指数等，取最近三年或最近三期）、"债务与评级"（存续债余额、到期分布、主体/债项评级、中债隐含评级）。同一指标多期各占一行（如2023/2024/2026H1三行），总条数不少于12条。
7. data_gaps：只写确实查询过但未获得的数据，并注明已尝试的渠道；不得把"没去查"写成数据缺口。

八、表述要求
1. 输出文本中不得出现JSON键名或数据库字段名（如city_flag、ISCITYINVERT、juyuan_city_flag、issuer_city_flag_any），一律用业务语言表述。
2. 不得出现"快照""S1快照"等技术性表述；来源引用只在evidence_refs中写S编号。
3. 默认摘要（brief）合计不超过1500个汉字：公司简介不超过120字（只写实控人/股权性质、核心业务、区域定位）；一句话信用判断不超过60字；核心变化与核心风险各最多3条、每条不超过120字；不足3条如实少输出。
4. 控制总体积：输出JSON全文不超过约8000字符（优先保证必填数据完整，压缩文字表述而非删减数据行）；juyuan_metrics 12—20条；operating_changes、all_risks各不超过6条；public_opinion_events不超过8条；每条summary、credit_impact、conclusion不超过100个汉字。
5. 严格只输出约定的JSON：不要Markdown围栏、不要解释、不要思考过程、不要任何JSON之外的文字。"""

TASK_TEMPLATE = """===任务===

请以bond_code对应的法律发行人为对象，完成一次发行人信用研究。

执行顺序：
1. 读取聚源数据快照，确认债券、主体、城投标志与分类起点；
2. 按"必查清单"用数据查询工具补齐财务（近三年+最新一期）、存续债与到期分布、评级；城投主体必须补齐区域经济财政数据；
3. 用检索工具按"发行人直接→区域/关联平台→行业"三层核查舆情；
4. 判断主体分类并选择分析框架，形成结论；
5. 输出约定JSON：一屏信用摘要（meta/classification/brief）+ 详实折叠依据（details，含债券基本情况bond_profile、区域财政表、多期财务指标）+ 来源（sources）。

默认摘要只保留：不超过120字的公司简介、一句话信用判断、最重要的3条边际变化、最重要的3项风险、重大舆情结论、后续最值得跟踪的指标（top_monitoring_items）。有效内容不足3条时如实少输出。

输出JSON结构（一级字段齐全，不适用的details子对象返回空对象）：
{{"meta":{{"bond_code":"","bond_name":"","issuer_name":"","issue_company_code":"","research_date":"","juyuan_query_date":"","latest_financial_period":"","data_completeness":"high|medium|low"}},"classification":{{"type":"CITY_PLATFORM|CITY_HYBRID|NON_CITY|UNCERTAIN","subtype":"","confidence":"high|medium|low","juyuan_city_flag":"是|否|缺失","reasons":[],"conflicting_evidence":[]}},"brief":{{"company_intro":"","one_sentence_conclusion":"","overall_trend":"improving|stable|weakening|uncertain","key_changes":[{{"direction":"positive|negative|neutral|uncertain","title":"","conclusion":"","evidence_refs":[]}}],"top_risks":[{{"severity":"high|medium|low|uncertain","title":"","conclusion":"","monitor":"","evidence_refs":[]}}],"public_opinion":{{"verdict":"found|not_found|insufficient","conclusion":""}},"top_monitoring_items":[]}},"details":{{"company_profile":{{}},"bond_profile":{{}},"city_analysis":{{}},"hybrid_analysis":{{}},"industry_analysis":{{}},"operating_changes":[],"all_risks":[],"public_opinion_events":[{{"date":"","level":"发行人|区域|关联平台|行业","title":"","summary":"","credit_impact":"","source_name":""}}],"juyuan_metrics":[{{"category":"主体财务|区域财政|行业数据|债务与评级","metric":"","value":"","unit":"","period":""}}],"data_gaps":[]}},"sources":[{{"ref":"S1","title":"","source_name":"聚源数据库或其他来源","source_type":"juyuan|announcement|rating_report|regulatory|roadshow|research_report|commentary|media|social","publish_date":"","report_period":"","is_primary":true}}]}}

bond_code={bond_code}
研究日期={research_date}

聚源数据快照（JSON，起点与校验基准，继续用数据查询工具补齐必查清单）：
{snapshot}"""


def build_prompt(bond_code: str, snapshot: dict, *, allow_search: bool = True, mode: str = "full") -> str:
    research_date = datetime.now().strftime("%Y-%m-%d")
    body = SYSTEM_INSTRUCTIONS
    if not allow_search:
        body += ("\n\n注意：本次运行没有数据查询与检索工具，上述“必查清单”无法执行："
                 "只能基于聚源数据快照分析，快照未覆盖的信息一律写入data_gaps并注明“未接入查询工具”，不得编造。")
    task = SECTION_TASKS.get(mode, TASK_TEMPLATE).format(
        bond_code=bond_code, research_date=research_date,
        snapshot=json.dumps(snapshot, ensure_ascii=False, indent=1),
    )
    return body + "\n\n" + task


# 单章节任务：只深挖一个章节，输出同一JSON结构但仅填充指定字段（其余留空），
# 由后端 merge_section_report 合并进已有报告。
SECTION_TASKS = {
    "profile": """===任务（只做"公司介绍"章节）===

本次只完成公司介绍与债券基本情况，不做行业/区域/舆情/风险分析。

1. 读取聚源数据快照；用数据查询工具补齐：实控人与股权层级、核心业务构成与收入结构、区域与集团定位、资产规模、本券发行条款（发行规模、当前余额、票面利率、期限与还本方式、评级、担保人、主承销商）。
2. 输出JSON：只需填充 meta、brief.company_intro（不超过120字）、details.company_profile、details.bond_profile、sources；其余字段一律返回空对象/空数组/空字符串。

输出JSON结构（同完整研究）：
{{"meta":{{...同完整研究...}},"classification":{{}},"brief":{{"company_intro":""}},"details":{{"company_profile":{{}},"bond_profile":{{}}}},"sources":[{{"ref":"S1","title":"","source_name":"","source_type":"juyuan|announcement|rating_report|regulatory|research_report|media","publish_date":"","report_period":"","is_primary":true}}]}}

bond_code={bond_code}
研究日期={research_date}

聚源数据快照（JSON）：
{snapshot}""",
    "changes": """===任务（只做"边际变化"章节）===

本次只完成经营与财务边际变化，不做舆情、分类论证与风险清单。

1. 读取聚源数据快照；用数据查询工具按"必查清单"补齐最近三年年报+最新一期的营业收入、净利润、货币资金、有息债务、短期有息债务、经营活动现金流净额、资产负债率、对外担保；城投主体同时补齐区域财政三个年度数据。
2. brief.key_changes输出最重要的3条边际变化（多期比较、具体数值与期间、信用含义）；details.operating_changes收纳其余有效变化；details.juyuan_metrics输出多期指标（category=主体财务/区域财政/债务与评级，不少于12条）；details.city_analysis在城投主体时填充区域财政要点。
3. 输出JSON：只需填充 meta、brief.key_changes、details.operating_changes、details.juyuan_metrics、details.city_analysis、sources；其余字段留空。sources不得为空：凡evidence_refs引用的S编号必须在sources中逐条给出对应来源（聚源查询、公告或检索资料）。

输出JSON结构（同完整研究）：
{{"meta":{{...同完整研究...}},"classification":{{}},"brief":{{"key_changes":[{{"direction":"positive|negative|neutral|uncertain","title":"","conclusion":"","evidence_refs":[]}}]}},"details":{{"operating_changes":[],"juyuan_metrics":[{{"category":"主体财务|区域财政|行业数据|债务与评级","metric":"","value":"","unit":"","period":""}}],"city_analysis":{{}}}},"sources":[{{...同完整研究...}}]}}

bond_code={bond_code}
研究日期={research_date}

聚源数据快照（JSON）：
{snapshot}""",
    "risks": """===任务（只做"风险"章节）===

本次只完成风险识别，不做舆情、公司介绍与边际变化。

1. 读取聚源数据快照；用数据查询工具补齐与风险相关的数据：债务结构、短债覆盖、到期分布、对外担保、受限资产、应收款与回款、（城投）区域财政与债务率。
2. brief.top_risks输出最重要的3项风险（severity、结论含发行人特定证据与传导路径、monitor监测指标）；details.all_risks收纳其余风险。
3. 输出JSON：只需填充 meta、brief.top_risks、details.all_risks、sources；其余字段留空。sources不得为空：凡evidence_refs引用的S编号必须在sources中逐条给出对应来源（聚源查询、公告或检索资料）。

输出JSON结构（同完整研究）：
{{"meta":{{...同完整研究...}},"classification":{{}},"brief":{{"top_risks":[{{"severity":"high|medium|low|uncertain","title":"","conclusion":"","monitor":"","evidence_refs":[]}}]}},"details":{{"all_risks":[]}},"sources":[{{...同完整研究...}}]}}

bond_code={bond_code}
研究日期={research_date}

聚源数据快照（JSON）：
{snapshot}""",
    "opinion": """===任务（只做"舆情核查"章节）===

本次只完成重大舆情核查，不做财务、分类与风险分析。

1. 用检索工具按"发行人直接负面→同城/关联平台→行业与政策"三层核查：发行人负面舆情决定brief.public_opinion（verdict三选一，found仅指违约/欠息/票据逾期/被执行/评级下调/重大诉讼/监管处罚/高管被查/负面媒体报道等风险类事件；正常发债、获批、评级维持等中性正面事件不得作为found依据）；负面事件逐条记入details.public_opinion_events（只收负面，level标注发行人/区域/关联平台/行业）。
2. 输出JSON：只需填充 meta、brief.public_opinion、details.public_opinion_events、sources；其余字段留空。

输出JSON结构（同完整研究）：
{{"meta":{{...同完整研究...}},"classification":{{}},"brief":{{"public_opinion":{{"verdict":"found|not_found|insufficient","conclusion":""}}}},"details":{{"public_opinion_events":[{{"date":"","level":"发行人|区域|关联平台|行业","title":"","summary":"","credit_impact":"","source_name":""}}]}},"sources":[{{...同完整研究...}}]}}

bond_code={bond_code}
研究日期={research_date}

聚源数据快照（JSON）：
{snapshot}""",
}


# --------------------------------------------------------------------------- #
# 聚源数据快照
# --------------------------------------------------------------------------- #
def resolve_bond(code: str) -> dict[str, Any] | None:
    """按代码定位静态缓存中的债券记录（复用债券详查的索引）。"""
    from bond_detail.service import _bond_indexes, _yield_indexes
    from broker_market.storage import normalize_code, bare_code

    code = normalize_code(str(code or "").strip())
    if not code:
        return None
    _, by_code, _ = _bond_indexes()
    bond = by_code.get(code) or by_code.get(bare_code(code))
    if not bond:
        return None
    resolved = dict(bond)
    resolved["code"] = normalize_code(bond.get("code"))
    yields, _ = _yield_indexes()
    current = yields.get(resolved["code"]) or yields.get(bare_code(resolved["code"]))
    resolved["current_yield"] = current
    return resolved


def issuer_key(bond: dict[str, Any]) -> str:
    company_code = str(bond.get("issue_company_code") or "").strip()
    if company_code:
        return f"cc_{company_code}"
    issuer = str(bond.get("issuer") or "").strip()
    digest = hashlib.sha1(issuer.encode("utf-8")).hexdigest()[:12] if issuer else "unknown"
    return f"name_{digest}"


def _org_type_label(conn, company_code: str) -> str:
    from juyuan_update.oracle_bonds import _fetch_entity_types

    if not company_code:
        return ""
    types = _fetch_entity_types(conn, [company_code])
    meta = types.get(str(company_code))
    return str(meta[0]) if meta else ""


def _oracle_snapshot_parts(raw_code: str, issuer: str) -> tuple[dict, list[dict], list[str]]:
    """实时查询聚源：单券主档标志、企业性质、发行人存续债与到期分布。失败逐项降级。"""
    from juyuan_update.db import connect

    gaps: list[str] = []
    master: dict = {}
    bonds: list[dict] = []
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT SYMBOL, COMPNAME, BONDTYPE2, RAISEMODE, ISCITYINVERT,
                       GUARANTOR, ISSUECOMPCODE, STARTDATE, MATURITYDATE, ISVALID
                FROM TQ_BD_NEWESTBASICINFO
                WHERE SYMBOL = :symbol AND ROWNUM = 1
                """,
                {"symbol": raw_code},
            )
            row = cur.fetchone()
            if row:
                master = {
                    "bond_type": str(row[2] or ""),
                    "raise_mode": str(row[3] or ""),
                    "juyuan_city_flag": {1: "是", 0: "否", "1": "是", "0": "否"}.get(row[4], "缺失"),
                    "guarantor": str(row[5] or ""),
                    "issue_company_code": str(row[6] or ""),
                }
                company_code = str(row[6] or "")
            else:
                master = {"juyuan_city_flag": "缺失"}
                company_code = ""
                gaps.append("聚源债券主档未查到该券（TQ_BD_NEWESTBASICINFO）")
            if company_code:
                master["org_type"] = _org_type_label(conn, company_code)
                cur.execute(
                    """
                    SELECT SYMBOL, BONDSNAME, BONDTYPE2, ISCITYINVERT, STARTDATE, MATURITYDATE
                    FROM TQ_BD_NEWESTBASICINFO
                    WHERE ISSUECOMPCODE = :cc AND ISVALID = '1' AND MATURITYDATE > TO_CHAR(SYSDATE, 'YYYYMMDD')
                    """,
                    {"cc": company_code},
                )
                today = datetime.now().date()
                for symbol, name, bond_type2, is_city, _start, maturity in cur.fetchall():
                    try:
                        maturity_dt = datetime.strptime(str(maturity)[:8], "%Y%m%d").date()
                    except ValueError:
                        continue
                    bonds.append({
                        "code": str(symbol or ""),
                        "name": str(name or ""),
                        "bond_type": str(bond_type2 or ""),
                        "city_flag": "是" if str(is_city or "") == "1" else "否",
                        "maturity_date": maturity_dt.isoformat(),
                        "remaining_years": round((maturity_dt - today).days / 365.0, 2),
                    })
                bonds.sort(key=lambda item: item["remaining_years"])
            else:
                gaps.append("缺少ISSUECOMPCODE，未查询发行人存续债明细")
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时快照仍可基于本地缓存
        gaps.append(f"聚源数据库查询失败：{str(exc)[:120]}")
    return master, bonds, gaps


def build_snapshot(bond: dict[str, Any]) -> tuple[dict, list[str]]:
    """组装聚源数据快照。返回 (snapshot, data_gaps)。"""
    from bond_detail.service import credit_facility_analysis, fetch_instrument_details, rating_compliance_analysis

    gaps: list[str] = []
    raw_code = str(bond.get("raw_code") or bond.get("code") or "").split(".")[0]
    issuer = str(bond.get("issuer") or "").strip()
    master, issuer_bonds, oracle_gaps = _oracle_snapshot_parts(raw_code, issuer)
    gaps.extend(oracle_gaps)
    instrument = {}
    try:
        instrument = fetch_instrument_details(raw_code) or {}
    except Exception:  # noqa: BLE001 - 票面条款缺失不阻断快照
        instrument = {}

    city_flags = [item["city_flag"] for item in issuer_bonds]
    city_any = "是" if (master.get("juyuan_city_flag") == "是" or "是" in city_flags) else (
        "否" if master.get("juyuan_city_flag") == "否" and city_flags and "是" not in city_flags else master.get("juyuan_city_flag") or "缺失"
    )
    maturity_buckets = {"within_1y": 0, "y1_3": 0, "beyond_3y": 0}
    for item in issuer_bonds:
        years = item.get("remaining_years") or 0
        if years <= 1:
            maturity_buckets["within_1y"] += 1
        elif years <= 3:
            maturity_buckets["y1_3"] += 1
        else:
            maturity_buckets["beyond_3y"] += 1

    facility = {}
    try:
        facility = credit_facility_analysis(bond) or {}
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"授信数据读取失败：{str(exc)[:120]}")
    compliance = {}
    try:
        compliance = rating_compliance_analysis(str(bond.get("code") or "")) or {}
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"630评级合规读取失败：{str(exc)[:120]}")

    if not bond.get("internal_rating"):
        gaps.append("内部评级缺失")
    financial_note = "聚源财务指标表尚未接入，请通过检索公告/评级报告补充，未获取到的写入data_gaps"

    snapshot = {
        "query_date": datetime.now().strftime("%Y-%m-%d"),
        "source": "聚源数据库(TQ_BD_NEWESTBASICINFO/TQ_COMP_ORGTYPE)+门户本地缓存",
        "bond": {
            "code": bond.get("code") or "",
            "name": bond.get("name") or "",
            "issuer": issuer,
            "bond_type": master.get("bond_type") or bond.get("bond_type2") or "",
            "issue_date": bond.get("issue_date") or "",
            "effective_maturity_date": bond.get("effective_maturity_date") or "",
            "remaining_term_years": bond.get("term"),
            "implied_rating": bond.get("implied_rating") or "",
            "internal_rating": bond.get("internal_rating") or "",
            "entity_nature": master.get("org_type") or bond.get("entity") or "",
            "juyuan_city_flag": master.get("juyuan_city_flag") or "缺失",
            "issuer_city_flag_any": city_any,
            "guarantor": master.get("guarantor") if master.get("guarantor") is not None else (bond.get("guarantor") or ""),
            "subordinated": bond.get("sub") or "",
            "current_cbd_valuation_yield": bond.get("current_yield"),
            "issue_company_code": master.get("issue_company_code") or "",
            "coupon_rate": instrument.get("coupon_rate"),
            "bond_maturity_date": instrument.get("maturity_date") or "",
            "put_date": instrument.get("put_date") or "",
            "payments_per_year": instrument.get("payments_per_year"),
        },
        "issuer": {
            "name": issuer,
            "outstanding_bond_count": len(issuer_bonds),
            "maturity_distribution": maturity_buckets,
            "outstanding_bonds": [
                {key: row.get(key) for key in ("code", "name", "bond_type", "city_flag", "maturity_date", "remaining_years")}
                for row in issuer_bonds[:40]
            ],
        },
        "portal_diagnostics": {
            "credit_facility": {
                "available": facility.get("available"),
                "internal_rating": facility.get("internal_rating") or "",
                "available_limit": facility.get("available_limit"),
                "data_date": facility.get("data_date") or "",
            },
            "rating_compliance_630": {
                "status": compliance.get("status") or "",
                "reason": compliance.get("reason") or "",
            },
        },
        "data_notes": [financial_note],
        "data_gaps": gaps,
    }
    return snapshot, gaps


# --------------------------------------------------------------------------- #
# 输出校验与归一化（需求文档 §13）
# --------------------------------------------------------------------------- #
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def han_chars(text: str) -> int:
    return len(_HAN_RE.findall(str(text or "")))


def extract_json_text(text: str) -> str:
    """从模型输出中提取 JSON 文本：优先代码围栏，其次首尾大括号截取。"""
    s = str(text or "").strip()
    fenced = re.findall(r"```(?:json)?\s*(.+?)\s*```", s, flags=re.S)
    candidates = [block for block in fenced if block.strip().startswith("{")]
    if candidates:
        s = max(candidates, key=len)
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        return s[start:end + 1]
    return s


def _valid_date(value: Any) -> bool:
    text = str(value or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clean_text_list(value: Any, limit: int = 8) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()][:limit]


_PERIOD_MARKS = ("H1", "H2", "Q1", "Q2", "Q3", "Q4", "中报", "半年", "三季", "一季")


def _norm_period(value: Any) -> str:
    """报告期归一化：提取年份并保留半年/季度标记。

    "邯郸市2025年"→"2025"，"2024末"→"2024"，"2026H1"/"2026中报"→"2026H1"。
    """
    s = str(value or "").strip()
    if not s:
        return ""
    match = re.search(r"(20\d{2})", s)
    if not match:
        return s
    year = match.group(1)
    upper = s.upper()
    if "H1" in upper or "中报" in s or "半年" in s or "一季" in s:
        return year + ("Q1" if "一季" in s else "H1")
    if "H2" in upper:
        return year + "H2"
    for mark in ("Q1", "Q2", "Q3", "Q4"):
        if mark in upper:
            return year + mark
    if "三季" in s:
        return year + "Q3"
    return year


_CATEGORY_CANONICAL = ("主体财务", "区域财政", "行业数据", "债务与评级")


def _norm_category(value: Any) -> str:
    """指标类别归一化：模型偶输出变体（如 subject财务/行业/评级）。"""
    s = str(value or "").strip()
    if s in _CATEGORY_CANONICAL:
        return s
    if "主体" in s or "财务" in s or "subject" in s.lower():
        return "主体财务"
    if "区域" in s or "财政" in s:
        return "区域财政"
    if "行业" in s or "景气" in s:
        return "行业数据"
    if "债务" in s or "评级" in s:
        return "债务与评级"
    return s or "主体财务"


def _evidence_ok(refs: Any, valid_refs: set[str]) -> list[str]:
    return [str(ref) for ref in _as_list(refs) if str(ref) in valid_refs]


def validate_and_normalize(report: dict, bond: dict[str, Any], *, mode: str = "full",
                           base: dict | None = None) -> tuple[dict | None, list[str]]:
    """结构校验 + 归一化。返回 (normalized, warnings)；不可修复的结构问题返回 (None, warnings)。

    章节模式（mode != "full"）下允许省略 classification 与 public_opinion：
    缺省时继承已有报告 base，或使用占位值，不视为结构失败。
    """
    warnings: list[str] = []
    if not isinstance(report, dict):
        return None, ["输出不是JSON对象"]
    missing = [key for key in ("meta", "classification", "brief", "details", "sources") if key not in report]
    if missing:
        if mode == "full":
            return None, [f"缺少一级字段 {', '.join(missing)}"]
        # 章节模式允许整体省略无关一级字段，按空值处理并由 base 继承
        for key in missing:
            report[key] = [] if key == "sources" else {}

    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    classification = report.get("classification") if isinstance(report.get("classification"), dict) else {}
    brief = report.get("brief") if isinstance(report.get("brief"), dict) else {}
    details = report.get("details") if isinstance(report.get("details"), dict) else {}

    base_report = (base or {}).get("report") if isinstance((base or {}).get("report"), dict) else (base or {})
    base_classification = base_report.get("classification") if isinstance(base_report.get("classification"), dict) else {}
    base_brief = base_report.get("brief") if isinstance(base_report.get("brief"), dict) else {}

    sources_raw = report.get("sources")
    if not isinstance(sources_raw, list):
        sources_raw = []
        warnings.append("sources缺失，已置空")
    base_sources = base_report.get("sources") if isinstance(base_report.get("sources"), list) else []
    inherited_sources = False
    if mode != "full" and not sources_raw and base_sources:
        # 章节输出常省略 sources 数组：引用编号按已有报告的来源校验，
        # 合并进已有报告后这些编号依然有效
        sources_raw = base_sources
        inherited_sources = True
    ctype = str(classification.get("type") or "").strip().upper()
    if ctype not in CLASSIFICATION_TYPES:
        inherited = str(base_classification.get("type") or "").strip().upper()
        if inherited in CLASSIFICATION_TYPES:
            ctype = inherited
        else:
            # 模型偶发输出空/非法分类：兜底为UNCERTAIN并警告，不再整单拒绝
            ctype = "UNCERTAIN"
            warnings.append(f"classification.type非法（{ctype!r}），已按待确认处理")
    confidence = str(classification.get("confidence") or "low").lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
        warnings.append("classification.confidence非法，已降为low")
    subtype = str(classification.get("subtype") or "").strip()
    if subtype and subtype not in SUBTYPE_OPTIONS:
        warnings.append(f"subtype非标准选项：{subtype}")

    verdict = str((brief.get("public_opinion") or {}).get("verdict") if isinstance(brief.get("public_opinion"), dict) else "")
    if verdict not in ("found", "not_found", "insufficient"):
        inherited = str((base_brief.get("public_opinion") or {}).get("verdict") or "")
        if inherited in ("found", "not_found", "insufficient"):
            verdict = inherited
        else:
            # 空verdict兜底为insufficient（无法判断），不再整单拒绝
            verdict = "insufficient"
            warnings.append(f"public_opinion.verdict非法（{verdict!r}），已按无法判断处理")
    trend = str(brief.get("overall_trend") or "uncertain")
    if trend not in ("improving", "stable", "weakening", "uncertain"):
        trend = "uncertain"
        warnings.append("overall_trend非法，已置uncertain")

    valid_refs = {
        str(item.get("ref") or item.get("id") or item.get("source_ref") or "").strip()
        for item in sources_raw if isinstance(item, dict)
    }
    valid_refs.discard("")
    key_changes, extra_changes = [], []
    for item in _as_list(brief.get("key_changes")):
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        direction = str(item.get("direction") or "uncertain")
        if direction not in DIRECTION_LABELS:
            direction = "uncertain"
        entry = {
            "direction": direction,
            "title": str(item.get("title") or "").strip(),
            "conclusion": str(item.get("conclusion") or "").strip(),
            "evidence_refs": _evidence_ok(item.get("evidence_refs"), valid_refs),
        }
        if item.get("evidence_refs") and not entry["evidence_refs"]:
            warnings.append(f"核心变化「{entry['title']}」引用编号不存在，已剔除")
        (key_changes if len(key_changes) < 3 else extra_changes).append(entry)
    top_risks, extra_risks = [], []
    for item in _as_list(brief.get("top_risks")):
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        severity = str(item.get("severity") or "uncertain")
        if severity not in ("high", "medium", "low", "uncertain"):
            severity = "uncertain"
        entry = {
            "severity": severity,
            "title": str(item.get("title") or "").strip(),
            "conclusion": str(item.get("conclusion") or "").strip(),
            "monitor": str(item.get("monitor") or "").strip(),
            "evidence_refs": _evidence_ok(item.get("evidence_refs"), valid_refs),
        }
        if item.get("evidence_refs") and not entry["evidence_refs"]:
            warnings.append(f"核心风险「{entry['title']}」引用编号不存在，已剔除")
        (top_risks if len(top_risks) < 3 else extra_risks).append(entry)
    if extra_changes:
        warnings.append(f"核心变化超出3条，{len(extra_changes)}条移入展开详情")
    if extra_risks:
        warnings.append(f"核心风险超出3项，{len(extra_risks)}项移入展开详情")

    public_opinion = brief.get("public_opinion") if isinstance(brief.get("public_opinion"), dict) else {}
    normalized_details = {
        "company_profile": details.get("company_profile") if isinstance(details.get("company_profile"), dict) else {},
        "bond_profile": details.get("bond_profile") if isinstance(details.get("bond_profile"), dict) else {},
        "city_analysis": details.get("city_analysis") if isinstance(details.get("city_analysis"), dict) else {},
        "hybrid_analysis": details.get("hybrid_analysis") if isinstance(details.get("hybrid_analysis"), dict) else {},
        "industry_analysis": details.get("industry_analysis") if isinstance(details.get("industry_analysis"), dict) else {},
        "operating_changes": [
            str(item) if not isinstance(item, dict) else item
            for item in _as_list(details.get("operating_changes"))
        ] + extra_changes,
        "all_risks": [
            str(item) if not isinstance(item, dict) else item
            for item in _as_list(details.get("all_risks"))
        ] + extra_risks,
        "public_opinion_events": _as_list(details.get("public_opinion_events")),
        "juyuan_metrics": [
            {**item,
             "category": _norm_category(item.get("category")),
             "period": _norm_period(item.get("period") or item.get("report_period"))}
            for item in _as_list(details.get("juyuan_metrics")) if isinstance(item, dict)
        ],
        "data_gaps": _clean_text_list(details.get("data_gaps"), limit=12),
    }

    summary_text = " ".join([
        str(brief.get("company_intro") or ""), str(brief.get("one_sentence_conclusion") or ""),
        *[item["conclusion"] for item in key_changes], *[item["conclusion"] for item in top_risks],
        str(public_opinion.get("conclusion") or ""),
    ])
    summary_chars = han_chars(summary_text)
    if summary_chars > 1500:
        warnings.append(f"摘要正文约{summary_chars}个汉字，超出1500上限")
    intro_chars = han_chars(brief.get("company_intro"))
    if intro_chars > 120:
        warnings.append(f"公司简介约{intro_chars}个汉字，超出120上限")
    conclusion_chars = han_chars(brief.get("one_sentence_conclusion"))
    if conclusion_chars > 60:
        warnings.append(f"一句话判断约{conclusion_chars}个汉字，超出60上限")

    sources = []
    for item in sources_raw:
        if not isinstance(item, dict):
            continue
        # 模型偶用变体键名（id/code/description），统一映射到约定结构
        ref = str(item.get("ref") or item.get("id") or item.get("source_ref") or "").strip()
        if not ref:
            continue
        title = str(item.get("title") or item.get("code") or item.get("name") or "").strip()
        model_source_type = str(item.get("source_type") or "").strip()
        source_name = str(item.get("source_name") or "").strip()
        if not source_name and model_source_type and model_source_type not in (
                "juyuan", "announcement", "rating_report", "regulatory", "roadshow",
                "research_report", "commentary", "media", "social"):
            source_name = model_source_type
        sources.append({
            "ref": ref,
            "title": title,
            "source_name": source_name,
            "source_type": model_source_type,
            "publish_date": str(item.get("publish_date") or item.get("date") or "").strip(),
            "report_period": str(item.get("report_period") or "").strip(),
            "is_primary": bool(item.get("is_primary")),
        })

    manual = classification.get("manual_override") if isinstance(classification.get("manual_override"), dict) else None
    normalized = {
        "meta": {
            "bond_code": str(bond.get("code") or meta.get("bond_code") or ""),
            "bond_name": str(bond.get("name") or meta.get("bond_name") or ""),
            "issuer_name": str(bond.get("issuer") or meta.get("issuer_name") or ""),
            "issue_company_code": str(meta.get("issue_company_code") or ""),
            "research_date": str(meta.get("research_date") or datetime.now().strftime("%Y-%m-%d")),
            "juyuan_query_date": str(meta.get("juyuan_query_date") or datetime.now().strftime("%Y-%m-%d")),
            "latest_financial_period": str(meta.get("latest_financial_period") or ""),
            "data_completeness": str(meta.get("data_completeness") or "medium"),
        },
        "classification": {
            "type": ctype,
            "subtype": subtype,
            "confidence": confidence,
            "juyuan_city_flag": str(classification.get("juyuan_city_flag") or "缺失"),
            "reasons": _clean_text_list(classification.get("reasons"), limit=6),
            "conflicting_evidence": _clean_text_list(classification.get("conflicting_evidence"), limit=6),
            "manual_override": manual if manual else None,
        },
        "brief": {
            "company_intro": str(brief.get("company_intro") or "").strip(),
            "one_sentence_conclusion": str(brief.get("one_sentence_conclusion") or "").strip(),
            "overall_trend": trend,
            "key_changes": key_changes,
            "top_risks": top_risks,
            "public_opinion": {
                "verdict": verdict,
                "conclusion": str(public_opinion.get("conclusion") or "").strip(),
            },
            "top_monitoring_items": _clean_text_list(brief.get("top_monitoring_items"), limit=5),
        },
        "details": normalized_details,
        "sources": sources,
    }
    if inherited_sources:
        normalized["_inherited_sources"] = True  # 临时标记：merge 时据此跳过重复追加
    if manual:
        normalized["classification"]["type"] = str(manual.get("type") or ctype)
    return normalized, warnings


def repair_json_with_local_llm(raw_text: str) -> str | None:
    """结构不合格时的一次格式修复：仅允许修复格式/压缩内容/保留引用，不得新增事实。"""
    try:
        from llm_config import available_providers

        providers = available_providers()
        if not providers:
            return None
        provider = providers[0]
        payload = {
            "model": provider["model"],
            "temperature": 0,
            "messages": [
                {"role": "system", "content":
                    "你是JSON修复器。把用户给的研究结果整理为合法JSON：只修复格式、压缩超限内容、"
                    "补齐已有引用编号，严禁新增任何事实、数据或观点。只输出JSON本身。"},
                {"role": "user", "content": raw_text[:24000]},
            ],
        }
        if provider.get("disable_thinking"):
            payload["thinking"] = {"type": "disabled"}
        if provider.get("chat_template_kwargs"):
            payload["chat_template_kwargs"] = provider["chat_template_kwargs"]
        req = urllib.request.Request(
            provider["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {provider['api_key']}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"] or {}).get("content") or None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 任务与缓存（全部落盘，兼容多 worker）
# --------------------------------------------------------------------------- #
_ACTIVE_STATES = {"running", STAGE_QUEUED, STAGE_JUYUAN, STAGE_AGENT, STAGE_CONCLUDE, STAGE_VALIDATE}


def _ensure_dirs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _job_path(issuer: str) -> Path:
    return JOBS_DIR / f"{issuer}.job.json"


def _report_path(issuer: str) -> Path:
    return REPORTS_DIR / f"{issuer}.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _job_view(job: dict) -> dict:
    """给前端的任务视图：按阶段推进时间补充进度，保持进度条持续前进。"""
    view = {
        "job_id": job.get("job_id"),
        "state": job.get("state"),
        "stage": job.get("stage"),
        "stage_text": STAGE_TEXT.get(job.get("stage") or "", job.get("stage")),
        "detail": job.get("detail") or "",
        "progress": job.get("progress") or 0,
        "plugin_calls": job.get("plugin_calls") or 0,
        "error": job.get("error") or "",
        "bond_code": job.get("bond_code"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
    }
    stage = job.get("stage")
    started = job.get("stage_started_at")
    view["progress"] = round(view["progress"], 1)
    if started and stage in (STAGE_JUYUAN, STAGE_AGENT, STAGE_CONCLUDE, STAGE_VALIDATE):
        elapsed = max(0.0, time.time() - float(started))
        chars = job.get("answer_chars") or 0
        if stage == STAGE_JUYUAN:
            view["progress"] = max(view["progress"], round(min(10.0, 3 + elapsed), 1))
        elif stage == STAGE_AGENT:
            creep = min(46.0, elapsed * 0.25 + (job.get("plugin_calls") or 0) * 2.0)
            view["progress"] = max(view["progress"], round(min(58.0, 10 + creep), 1))
        elif stage == STAGE_CONCLUDE:
            creep = min(31.0, elapsed * 0.25 + chars / 220.0)
            view["progress"] = max(view["progress"], round(min(94.0, 62 + creep), 1))
        elif stage == STAGE_VALIDATE:
            view["progress"] = max(view["progress"], 96.0)
    return view


def get_running_job(issuer: str) -> dict | None:
    job = _read_json(_job_path(issuer))
    if not job:
        return None
    if job.get("state") in _ACTIVE_STATES:
        updated = job.get("updated_ts") or 0
        if time.time() - float(updated) > JOB_STALE_SECONDS:
            return None  # 僵死任务（进程中断），允许接管
        return job
    return None


def get_latest_job(issuer: str) -> dict | None:
    """最近一次任务（含失败态，供前端展示错误与上一版结果提示）。"""
    job = _read_json(_job_path(issuer))
    if not job:
        return None
    if job.get("state") in _ACTIVE_STATES and time.time() - float(job.get("updated_ts") or 0) > JOB_STALE_SECONDS:
        return None
    return job


def fail_orphan_jobs() -> int:
    """服务启动时清理孤儿任务：上一进程遗留的运行中任务已无线程执行，
    标记失败避免被复用成"永远不动的进度条"。返回清理数量。"""
    _ensure_dirs()
    count = 0
    for path in JOBS_DIR.glob("*.job.json"):
        job = _read_json(path)
        if job and job.get("state") in _ACTIVE_STATES:
            job["state"] = STAGE_FAILED
            job["stage"] = STAGE_FAILED
            job["error"] = "服务重启导致任务中断，请重新生成"
            job["detail"] = ""
            job["updated_at"] = _now_iso()
            job["updated_ts"] = time.time()
            _write_json(path, job)
            count += 1
    return count


def get_job_by_id(job_id: str) -> dict | None:
    _ensure_dirs()
    for path in JOBS_DIR.glob("*.job.json"):
        job = _read_json(path)
        if job and job.get("job_id") == job_id:
            return job
    return None


def get_cached_report(issuer: str) -> dict | None:
    return _read_json(_report_path(issuer))


def start_research(bond: dict[str, Any], *, force: bool = False, mode: str = "full") -> dict:
    """启动（或复用正在运行的）研究任务；force 时忽略 TTL 直接重跑。

    同一发行人已有未完成任务时一律复用（force 也不重复起线程，避免同键竞争）。
    mode 为 SECTION_MODES 之一：full=完整研究，其余为单章节深挖并合并进已有报告。
    """
    if mode not in SECTION_MODES:
        raise ValueError(f"不支持的研究模式：{mode}")
    _ensure_dirs()
    issuer = issuer_key(bond)
    with _start_lock:
        running = get_running_job(issuer)
        if running:
            return _job_view(running)
        cached = get_cached_report(issuer)
        if cached and not force:
            generated = str(cached.get("generated_at") or "")
            try:
                age = datetime.now() - datetime.strptime(generated, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                age = timedelta(days=999)
            if age < timedelta(days=REPORT_TTL_DAYS):
                return {"state": "done", "cached": True, "progress": 100, "stage": STAGE_DONE,
                        "stage_text": STAGE_TEXT[STAGE_DONE], "detail": "缓存有效，直接展示"}

        job_id = f"{int(time.time() * 1000):x}-{hashlib.sha1(os.urandom(8)).hexdigest()[:8]}"
        job = {
            "job_id": job_id,
            "issuer_key": issuer,
            "bond_code": bond.get("code") or "",
            "mode": mode,
            "state": STAGE_QUEUED,
            "stage": STAGE_QUEUED,
            "detail": "任务已受理",
            "progress": 2,
            "plugin_calls": 0,
            "answer_chars": 0,
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
            "updated_ts": time.time(),
            "stage_started_at": time.time(),
            "error": "",
        }
        _write_json(_job_path(issuer), job)
        thread = threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"credit-research-{issuer}")
        thread.start()
    return _job_view(job)


def _update_job(job: dict, *, state: str | None = None, stage: str | None = None,
                detail: str | None = None, progress: float | None = None, **extra: Any) -> None:
    if state is not None:
        job["state"] = state
    if stage is not None:
        job["stage"] = stage
        job["stage_started_at"] = time.time()
        if stage in (STAGE_JUYUAN, STAGE_AGENT, STAGE_VALIDATE):
            job["state"] = "running"
    if detail is not None:
        job["detail"] = detail
    if progress is not None:
        job["progress"] = progress
    job.update(extra)
    job["updated_at"] = _now_iso()
    job["updated_ts"] = time.time()
    _write_json(_job_path(job["issuer_key"]), job)


def _on_agent_event(job: dict):
    def handler(kind: str, payload: dict) -> None:
        if kind == "plugin":
            job["plugin_calls"] = payload.get("count") or job.get("plugin_calls")
            _update_job(job, detail=f"检索补充资料：已完成{job['plugin_calls']}次查询")
        elif kind == "answer_delta":
            job["answer_chars"] = payload.get("chars") or 0
            # 累计输出超过300字才视为进入“生成结论”：Agent 检索期会先吐出
            # 较短的过程性消息，避免阶段标签来回跳
            first = job.get("stage") != STAGE_CONCLUDE and (job["answer_chars"] or 0) >= 300
            progress = max(job.get("progress") or 0, min(94.0, 62 + min(30.0, (job["answer_chars"] or 0) / 220.0)))
            if first or (job.get("stage") == STAGE_CONCLUDE and (job["answer_chars"] or 0) % 400 < 40):
                _update_job(job, stage=STAGE_CONCLUDE if first else None,
                            detail=f"生成结论中（已输出约{job['answer_chars']}字）", progress=progress)
        elif kind == "agent_error":
            job["agent_fallback"] = payload.get("message") or ""
    return handler


def _next_ref_map(new_sources: list[dict], existing_refs: set[str]) -> tuple[list[dict], dict[str, str]]:
    """章节输出的来源重新编号（接在已有来源之后），返回 (新来源列表, 旧ref→新ref映射)。"""
    numbers = []
    for ref in existing_refs:
        m = re.fullmatch(r"S(\d+)", str(ref))
        if m:
            numbers.append(int(m.group(1)))
    start = max(numbers, default=0)
    remapped, mapping = [], {}
    for item in new_sources:
        start += 1
        new_ref = f"S{start}"
        mapping[item["ref"]] = new_ref
        remapped.append({**item, "ref": new_ref})
    return remapped, mapping


def merge_section_report(base_payload: dict | None, section: dict, mode: str) -> dict:
    """把单章节结果合并进已有报告；无已有报告时按章节结果生成部分报告。

    来源重新编号避免与已有引用冲突；合并字段的 evidence_refs 同步改写。
    """
    import copy

    if not base_payload or not isinstance(base_payload.get("report"), dict):
        section.setdefault("meta", {})
        section["partial"] = True
        section["partial_modes"] = [mode]
        return section
    merged = copy.deepcopy(base_payload["report"])
    old_report = base_payload["report"]
    existing_refs = {str(s.get("ref") or "") for s in merged.get("sources") or []}
    if section.pop("_inherited_sources", False):
        # 来源是校验时从已有报告继承的（模型未自带 sources），不重复追加
        section_sources = []
    else:
        section_sources = section.get("sources") or []
    new_sources, ref_map = _next_ref_map(copy.deepcopy(section_sources), existing_refs)
    merged["sources"] = list(merged.get("sources") or []) + new_sources

    def remap(refs: Any) -> list[str]:
        return [ref_map.get(str(r), str(r)) for r in refs or []]

    brief, section_brief = merged.setdefault("brief", {}), section.get("brief") or {}
    details, section_details = merged.setdefault("details", {}), section.get("details") or {}
    if mode == "profile":
        if section_brief.get("company_intro"):
            brief["company_intro"] = section_brief["company_intro"]
        for key in ("company_profile", "bond_profile"):
            if section_details.get(key):
                details[key] = section_details[key]
    elif mode == "changes":
        if section_brief.get("key_changes"):
            brief["key_changes"] = [{**c, "evidence_refs": remap(c.get("evidence_refs"))}
                                    for c in section_brief["key_changes"]]
        if section_details.get("operating_changes"):
            details["operating_changes"] = section_details["operating_changes"]
        if section_details.get("juyuan_metrics"):
            # 按指标+归一化报告期联合合并：章节刷新只增不减，弱化的一次运行不会冲掉既有年度数据
            def metric_key(row: dict) -> tuple[str, str, str]:
                return (_norm_category(row.get("category")), str(row.get("metric") or ""),
                        _norm_period(row.get("period") or row.get("report_period")))
            by_key = {metric_key(row): row for row in details.get("juyuan_metrics") or [] if isinstance(row, dict)}
            for row in section_details["juyuan_metrics"]:
                if isinstance(row, dict):
                    normalized_row = {**row,
                                      "category": _norm_category(row.get("category")),
                                      "period": _norm_period(row.get("period") or row.get("report_period"))}
                    by_key[metric_key(normalized_row)] = normalized_row
            details["juyuan_metrics"] = list(by_key.values())
        if section_details.get("city_analysis"):
            details["city_analysis"] = {**(details.get("city_analysis") or {}), **section_details["city_analysis"]}
    elif mode == "risks":
        if section_brief.get("top_risks"):
            brief["top_risks"] = [{**r, "evidence_refs": remap(r.get("evidence_refs"))}
                                  for r in section_brief["top_risks"]]
        if section_details.get("all_risks"):
            details["all_risks"] = section_details["all_risks"]
    elif mode == "opinion":
        if section_brief.get("public_opinion"):
            brief["public_opinion"] = section_brief["public_opinion"]
        if section_details.get("public_opinion_events") is not None:
            details["public_opinion_events"] = section_details["public_opinion_events"]
    merged["classification"] = old_report.get("classification") or section.get("classification") or merged.get("classification")
    return merged


def _run_job(job: dict) -> None:
    from bond_detail.service import _bond_indexes
    from broker_market.storage import normalize_code

    try:
        _, by_code, _ = _bond_indexes()
        bond = by_code.get(normalize_code(job["bond_code"])) or by_code.get(job["bond_code"].split(".")[0])
        if not bond:
            raise RuntimeError("未找到该债券，无法生成研究")
        bond = dict(bond)

        _update_job(job, stage=STAGE_JUYUAN, state="running", detail="正在查询聚源数据库", progress=4)
        snapshot, gaps = build_snapshot(bond)
        gap_note = f"；快照缺口{len(gaps)}项" if gaps else ""
        _update_job(job, detail=f"聚源快照完成{gap_note}", progress=10)

        mode = job.get("mode") or "full"
        base_payload = get_cached_report(job["issuer_key"])
        allow_search = agent_configured()
        prompt = build_prompt(bond["code"], snapshot, allow_search=allow_search, mode=mode)
        _update_job(job, stage=STAGE_AGENT, state="running",
                    detail="已提交公司Agent，等待检索与生成", progress=10)
        raw_text, meta = run_research_prompt(prompt, _on_agent_event(job))
        if "{" not in raw_text:
            # Agent偶发提前结束（只输出过程性文字没有最终JSON）：同一任务重试一次
            _update_job(job, stage=STAGE_AGENT, detail="Agent提前结束，自动重试一次",
                        progress=max(job.get("progress") or 0, 20))
            job["answer_chars"] = 0
            raw_text, meta = run_research_prompt(prompt, _on_agent_event(job))
        _update_job(job, stage=STAGE_VALIDATE, state="running", detail="结构校验与归一化", progress=96)

        base_for_validate = {"report": base_payload.get("report")} if base_payload and isinstance(base_payload.get("report"), dict) else None
        parsed = None
        warnings: list[str] = []
        json_text = extract_json_text(raw_text)
        try:
            (REPORTS_DIR / f"{job['issuer_key']}.lastraw.txt").write_text(raw_text[:30000], encoding="utf-8")
        except OSError:
            pass
        try:
            parsed, warnings = validate_and_normalize(json.loads(json_text), bond, mode=mode, base=base_for_validate)
        except ValueError:
            parsed = None
        if parsed is None:
            repaired = repair_json_with_local_llm(raw_text)
            if repaired:
                try:
                    parsed, warnings = validate_and_normalize(
                        json.loads(extract_json_text(repaired)), bond, mode=mode, base=base_for_validate)
                    warnings.append("输出已经一次格式修复")
                except ValueError:
                    parsed = None
        if parsed is None:
            try:
                (REPORTS_DIR / f"{job['issuer_key']}.lastraw.txt").write_text(raw_text[:30000], encoding="utf-8")
            except OSError:
                pass
            raise RuntimeError("Agent输出未通过结构校验，已保留上一版结果")

        if mode != "full":
            parsed = merge_section_report(base_payload, parsed, mode)
            warnings.append(f"已将“{SECTION_LABELS.get(mode, mode)}”章节合并入报告")

        report_payload = {
            "generated_at": _now_iso() if mode == "full" else (base_payload or {}).get("generated_at") or _now_iso(),
            "channel": meta.get("channel") or "",
            "model": meta.get("model") or "",
            "plugin_calls": meta.get("plugin_calls") or 0,
            "bond_code": bond.get("code") or "",
            "issuer": bond.get("issuer") or "",
            "issuer_key": job["issuer_key"],
            "snapshot_gaps": gaps,
            "validation": {"warnings": warnings, "summary_han_chars": han_chars(
                " ".join([
                    parsed["brief"]["company_intro"], parsed["brief"]["one_sentence_conclusion"],
                    *[c["conclusion"] for c in parsed["brief"]["key_changes"]],
                    *[r["conclusion"] for r in parsed["brief"]["top_risks"]],
                    parsed["brief"]["public_opinion"]["conclusion"],
                ]))},
            "report": parsed,
        }
        report_payload["mode"] = mode
        report_payload["sections"] = {**((base_payload or {}).get("sections") or {}),
                                      **({mode: _now_iso()} if mode != "full" else {})}
        _write_json(_report_path(job["issuer_key"]), report_payload)
        _update_job(job, state=STAGE_DONE, stage=STAGE_DONE, progress=100,
                    detail=f"{SECTION_LABELS.get(mode, '研究')}完成",
                    channel=meta.get("channel") or "", model=meta.get("model") or "",
                    job_id=job["job_id"])
    except Exception as exc:  # noqa: BLE001 - 任何失败都保留上一版成功报告
        _update_job(job, state=STAGE_FAILED, stage=STAGE_FAILED, detail="", error=str(exc)[:300])


# --------------------------------------------------------------------------- #
# 人工覆盖分类（FR-04）
# --------------------------------------------------------------------------- #
def override_classification(bond: dict[str, Any], new_type: str, note: str) -> dict | None:
    if new_type not in CLASSIFICATION_TYPES:
        raise ValueError("不支持的主体分类")
    issuer = issuer_key(bond)
    with _start_lock:
        payload = get_cached_report(issuer)
        if not payload or not isinstance(payload.get("report"), dict):
            return None
        report = payload["report"]
        classification = report.setdefault("classification", {})
        if not classification.get("manual_override"):
            classification["auto_type"] = classification.get("type") or ""
        classification["manual_override"] = {
            "type": new_type,
            "note": str(note or "").strip()[:200],
            "operator": "门户登录用户",
            "at": _now_iso(),
        }
        classification["type"] = new_type
        payload["override_updated_at"] = _now_iso()
        _write_json(_report_path(issuer), payload)
    return payload
