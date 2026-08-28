# -*- coding: utf-8 -*-
"""从内评门户（magicflu）自动抓取每日内评/授信/持仓数据。

替代手工上传统一 Excel 的 neiping 表与「是否持仓」列：
- 内评与对手限额：空间内「全部每日有效主体授信」表（有效主体名称 / 主体评级 / 最新主体可用投资限额）。
- 债项持仓：空间内「债项评级-有效1」表（证券代码 / 持仓日期 / 持仓金额（债项）），
  持仓金额非 0 判定为有持仓。
- 全部数据采取「永远取最新可用日期」策略。

链路：IAM 统一认证（OAuth2 密码模式，密码 AES-CFB 加密）
      → SSO 换 magicflu 会话 → 表单记录 feed（按 updated 倒序拉取）。

用法：
    from .neiping_portal_fetch import update_portal_data
    update_portal_data(progress=callback)
"""
from __future__ import annotations

import base64
import re
import urllib.parse
import warnings
from datetime import datetime

import requests
from cryptography.hazmat.decrepit.ciphers.modes import CFB
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

from . import config
from .unified_excel import save_counterparty_limits, write_json

warnings.filterwarnings("ignore")

IAM_BASE = "https://iam.hxjyam.com"
PORTAL_BASE = "http://10.6.60.110:999"
SPACE_ID = "00000000-0000-0000-0000-000000000000"

# 「全部每日有效主体授信」表单：内评级别 + 对手限额
LIMIT_FORM_ID = "d919a133-ff5b-4c86-92e3-52e3f298368f"
# 「债项评级-有效1」表单：债项维度最新持仓金额
BOND_FORM_ID = "57f2ed4e-ae0e-4bb7-ba17-70a5616654c8"

IAM_USERNAME = "zhenghongbin"
IAM_PASSWORD = "Abcd123%"
_AES_KEY = b"ruijiancloudbase"  # 与门户前端加密密钥一致

FIELD_ISSUER = "youxiaozhutimingcheng"       # 有效主体名称
FIELD_RATING = "zhutipingji"                 # 主体评级
FIELD_DATE = "jinririqi"                     # 授信表数据日期
# neipingsheet「最新可用对手限额」的来源字段
FIELD_AVAIL_LIMIT = "zuixinzhutikeyongtouzixiane"  # 最新主体可用投资限额

BOND_FIELD_CODE = "zhengquandaima"           # 证券代码
BOND_FIELD_NAME = "zhaixiangmingcheng"       # 债项名称
BOND_FIELD_ISSUER = "rongzizhuti"            # 融资主体
BOND_FIELD_AMOUNT = "chicangjinezhaixiang"   # 持仓金额（债项）
BOND_FIELD_HOLDING_DATE = "chicangriqi"      # 持仓日期
BOND_FIELD_REFRESH_DATE = "jinririqi"        # 表单刷新日期

_PAGE_SIZE = 4000
_MAX_PAGES = 20


def _encrypt_password(plain: str) -> str:
    encryptor = Cipher(algorithms.AES(_AES_KEY), CFB(_AES_KEY)).encryptor()
    raw = encryptor.update(plain.encode()) + encryptor.finalize()
    return base64.b64encode(raw).decode()


def login() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    basic = "Basic " + base64.b64encode(b"rui:rui").decode()
    params = {
        "username": IAM_USERNAME,
        "randomStr": "blockPitrix",
        "code": "1",
        "grant_type": "password",
        "scope": "server",
    }
    r = s.post(
        f"{IAM_BASE}/api/auth/token/form",
        params=params,
        data={"password": _encrypt_password(IAM_PASSWORD)},
        headers={"Authorization": basic, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    if not r.json().get("success"):
        raise RuntimeError("IAM 登录失败: " + r.text[:200])
    # SSO 换 magicflu 会话（跟随 code 回调建立 JSESSIONID）
    sso = (
        f"{IAM_BASE}/api/auth/oauth2/authorize?scope=server&client_id=crms"
        "&response_type=code&redirect_uri=http%3A%2F%2F10.6.60.110%3A999%2Fmagicflu&state=xxx"
    )
    r = s.get(sso, allow_redirects=True, timeout=30)
    r.raise_for_status()
    if "JSESSIONID" not in s.cookies:
        raise RuntimeError("magicflu 会话建立失败")
    return s


def _feed_records(s: requests.Session, form_id: str, start: int, limit: int) -> list[dict]:
    bq = urllib.parse.quote("updated(orderby):desc")
    url = (
        f"{PORTAL_BASE}/magicflu/service/s/{SPACE_ID}/forms/{form_id}"
        f"/records/feed?start={start}&limit={limit}&bq={bq}"
    )
    r = s.get(url, timeout=180)
    r.raise_for_status()
    records = []
    for m in re.finditer(r'<content type="xml"><record[^>]*>(.*?)</record>', r.text, re.S):
        rec = {}
        for fm in re.finditer(r"<([a-zA-Z_0-9]+)>(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))</\1>", m.group(1), re.S):
            rec[fm.group(1)] = fm.group(2) if fm.group(2) is not None else fm.group(3)
        if rec:
            records.append(rec)
    return records


def _fetch_by_latest_date(s: requests.Session, form_id: str, date_field: str) -> list[dict]:
    """按 updated 倒序翻页，只保留最新非空日期的记录（服务端不支持字段值过滤）。"""
    kept, latest = [], None
    for page in range(_MAX_PAGES):
        records = _feed_records(s, form_id, page * _PAGE_SIZE, _PAGE_SIZE)
        if not records:
            break
        for rec in records:
            dt = (rec.get(date_field) or "").strip()
            if latest is None:
                if not dt:
                    continue
                latest = dt
            if dt == latest:
                kept.append(rec)
        if latest is not None and (records[-1].get(date_field) or "").strip() != latest:
            break  # 已越过最新日期，覆盖完整
        if len(records) < _PAGE_SIZE:
            break
    if not kept:
        raise RuntimeError("门户记录拉取为空: " + form_id)
    return kept


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_limits(s: requests.Session) -> dict:
    """内评级别 + 对手限额（永远取最新可用日期）。"""
    records = _fetch_by_latest_date(s, LIMIT_FORM_ID, FIELD_DATE)
    limits, ratings = {}, {}
    for rec in records:
        issuer = (rec.get(FIELD_ISSUER) or "").strip()
        if not issuer:
            continue
        amount = _to_float(rec.get(FIELD_AVAIL_LIMIT))
        if amount is not None:
            limits[issuer] = round(amount, 6)
        rating = (rec.get(FIELD_RATING) or "").strip()
        if rating:
            ratings[issuer] = rating
    return {
        "date": records[0].get(FIELD_DATE, ""),
        "total_records": len(records),
        "limits": limits,
        "ratings": ratings,
    }


def fetch_bond_holdings(s: requests.Session) -> dict:
    """债项最新持仓金额（永远取最新可用日期，非 0 即有持仓）。"""
    records = _fetch_by_latest_date(s, BOND_FORM_ID, BOND_FIELD_REFRESH_DATE)
    bonds = {}
    for rec in records:
        raw_code = (rec.get(BOND_FIELD_CODE) or "").strip()
        if not raw_code:
            continue
        amount = _to_float(rec.get(BOND_FIELD_AMOUNT))
        if amount is None:
            continue
        bonds[raw_code] = {
            "name": (rec.get(BOND_FIELD_NAME) or "").strip(),
            "issuer": (rec.get(BOND_FIELD_ISSUER) or "").strip(),
            "amount": round(amount, 6),
            "is_holding": amount != 0,
            "holding_date": (rec.get(BOND_FIELD_HOLDING_DATE) or "").strip(),
        }
    refresh_dates = [r.get(BOND_FIELD_REFRESH_DATE, "") for r in records if r.get(BOND_FIELD_REFRESH_DATE)]
    return {
        "refresh_date": max(refresh_dates) if refresh_dates else "",
        "total_records": len(records),
        "bonds": bonds,
    }


def save_portal_data(limits_payload: dict, holdings_payload: dict) -> dict:
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "magicflu portal",
        "limits_date": limits_payload["date"],
        "limits_total_records": limits_payload["total_records"],
        "limits": limits_payload["limits"],
        "ratings": limits_payload["ratings"],
        "holdings_refresh_date": holdings_payload["refresh_date"],
        "holdings_total_records": holdings_payload["total_records"],
        "holdings": holdings_payload["bonds"],
    }
    write_json(config.PORTAL_DATA_JSON, payload)
    # 对手限额沿用既有 JSON，下游消费方无需改动
    save_counterparty_limits(
        limits_payload["limits"],
        f"portal:{limits_payload['date']}",
    )
    return payload


def load_portal_data() -> dict:
    from .unified_excel import load_json
    return load_json(config.PORTAL_DATA_JSON, {})


def update_portal_data(progress=None) -> dict:
    """登录门户并刷新内评/限额/持仓缓存，供每日更新任务调用。"""

    def log(message):
        if progress:
            progress(message)

    log("登录信评门户（IAM 统一认证）")
    session = login()
    log("拉取最新一日主体授信与内评")
    limits_payload = fetch_limits(session)
    log(f"主体授信 {len(limits_payload['limits'])} 家（数据日期 {limits_payload['date']}），拉取债项最新持仓")
    holdings_payload = fetch_bond_holdings(session)
    holding_count = sum(1 for b in holdings_payload["bonds"].values() if b["is_holding"])
    payload = save_portal_data(limits_payload, holdings_payload)
    log(
        f"门户数据更新完成：授信主体 {len(payload['limits'])} 家（{payload['limits_date']}），"
        f"持仓债项 {holding_count}/{len(payload['holdings'])} 只（刷新日期 {payload['holdings_refresh_date']}）"
    )
    return payload


if __name__ == "__main__":
    import json

    result = update_portal_data()
    print(json.dumps({
        "limits_date": result["limits_date"],
        "limit_issuers": len(result["limits"]),
        "holdings_refresh_date": result["holdings_refresh_date"],
        "holding_bonds": sum(1 for b in result["holdings"].values() if b["is_holding"]),
        "total_bonds": len(result["holdings"]),
    }, ensure_ascii=False, indent=2))
