# -*- coding: utf-8 -*-
"""PDF 活跃内容清洗（渗透测试 pdf-xss 整改）。

浏览器内置 PDF 查看器会执行文档内嵌的 JavaScript（/JavaScript 名称树、
/OpenAction、/AA、/Launch 等动作对象）。知识库的在线预览把 PDF 以同源
iframe 内嵌（app.js），被植入脚本的 PDF 在预览时即可弹窗/跳转，构成 XSS。
本模块在文件层剥离这些活跃对象：

- 文档级脚本：Catalog 的 /Names /JavaScript 名称树、AcroForm /XFA；
- 动作对象：任意 xref 上的 /JavaScript、/JS、/Launch、/RichMedia 键，以及
  事件触发用的 /AA（Additional Actions）；
- 自动执行动作：Catalog 与页面对象上的 /OpenAction——仅当其指向脚本或
  /Launch 类动作时移除，纯 /GoTo /Dest 页面跳转保留。

清洗命中的文件按 garbage collect + deflate 重写（先写临时文件再原子替换）；
未命中任何风险键的文件保持原样不重写（内容哈希不变，对应预览缓存键不受影响）。
"""

import os
import re
import tempfile

import pymupdf

# /JS 需避免误伤 /JS0 之类的资源名：后面不能紧跟字母数字
_RISK_KEY_RE = re.compile(r"/(JavaScript|JS|Launch)(?![0-9A-Za-z])")

# 出现风险对象时直接置空的键
_NULL_KEYS = ("JavaScript", "JS", "Launch", "AA", "XFA", "RichMedia")

_CLEAN_REPORT_CAP = 200


class PdfGuardError(Exception):
    """PDF 无法安全解析/处理（如加密或损坏），调用方应拒绝该文件。"""


def _xref_source(doc, xref):
    try:
        return doc.xref_object(xref, compressed=False) or ""
    except Exception:
        return ""


def _null_key(doc, xref, key, removed):
    try:
        typ, _value = doc.xref_get_key(xref, key)
    except Exception:
        return
    if typ in ("null", "none"):
        return
    try:
        doc.xref_set_key(xref, key, "null")
        removed.append(f"xref {xref}: /{key}")
    except Exception:
        pass


def _null_open_action_if_risky(doc, xref, removed):
    """/OpenAction 仅在指向脚本或 /Launch 类动作时置空，正常 /GoTo /Dest 保留。"""
    try:
        typ, value = doc.xref_get_key(xref, "OpenAction")
    except Exception:
        return
    if typ in ("null", "none") or not value:
        return
    target = str(value)
    ref = re.match(r"^(\d+)\s+\d+\s+R$", target.strip())
    if ref:
        target = _xref_source(doc, int(ref.group(1)))
    if _RISK_KEY_RE.search(target):
        try:
            doc.xref_set_key(xref, "OpenAction", "null")
            removed.append(f"xref {xref}: /OpenAction")
        except Exception:
            pass


def _apply_all(doc):
    """对已打开的文档执行全部清理动作，返回移除清单。"""
    removed = []
    catalog = doc.pdf_catalog()
    for path_key in ("Names/JavaScript", "AcroForm/XFA"):
        try:
            typ, _value = doc.xref_get_key(catalog, path_key)
            if typ not in ("null", "none"):
                doc.xref_set_key(catalog, path_key, "null")
                removed.append(f"catalog: /{path_key}")
        except Exception:
            pass
    for xref in range(1, doc.xref_length()):
        source = _xref_source(doc, xref)
        if not source or source == "null" or not _RISK_KEY_RE.search(source):
            continue
        for key in _NULL_KEYS:
            _null_key(doc, xref, key, removed)
    _null_open_action_if_risky(doc, catalog, removed)
    _null_key(doc, catalog, "AA", removed)
    for page in doc:
        _null_open_action_if_risky(doc, page.xref, removed)
        _null_key(doc, page.xref, "AA", removed)
    return removed


def sanitize_pdf(path, rewrite=True):
    """清洗单个 PDF 文件，返回 {"modified": bool, "removed": [str]}。

    rewrite=False 时只扫描不写盘（用于检测）。文件无法解析/带密码时抛出
    PdfGuardError，由调用方决定拒绝。
    """
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise PdfGuardError(f"PDF 文件无法解析: {exc}") from exc
    try:
        if doc.is_encrypted or doc.needs_pass:
            raise PdfGuardError("PDF 文件带密码保护，不支持上传")
        removed = _apply_all(doc)
        if not removed:
            return {"modified": False, "removed": []}
        if not rewrite:
            return {"modified": True, "removed": removed[:_CLEAN_REPORT_CAP]}
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".pdf", dir=os.path.dirname(os.path.abspath(path)) or ".")
        os.close(tmp_fd)
        try:
            doc.save(tmp_path, garbage=3, deflate=True)
            # Windows：pymupdf 持有源文件句柄时无法原子替换，先关再换
            doc.close()
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return {"modified": True, "removed": removed[:_CLEAN_REPORT_CAP]}
    finally:
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass
