# -*- coding: utf-8 -*-
"""存量路演报告作者/机构大模型回填。

历史路演报告没有"报告作者/报告机构"字段，本脚本逐篇读取报告文件、抽取文本，
调用大模型（默认 MiMo，DeepSeek 兜底）识别作者与机构并写回报告元信息。
机构识别复用路演简称对照表（如"兴证"→"兴业证券"）。

用法（在项目根目录、配置好 MIMO_API_KEY 环境变量后运行）：
    python scripts/backfill_roadshow_meta.py            # 执行回填
    python scripts/backfill_roadshow_meta.py --dry-run  # 只看将要回填的结果
    python scripts/backfill_roadshow_meta.py --report-id <id>  # 只处理指定报告
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from internal_knowledge_base import routes as R  # noqa: E402
from internal_knowledge_base.storage import SQLiteStore  # noqa: E402


def backfill(dry_run=False, report_id=None):
    store = SQLiteStore(Path(R.STORE_PATH), R._default_reminder_config())
    targets = [
        report for report in store.reports(include_deleted=False)
        if report.get("reportType") == "roadshow"
        and (not report.get("sourceAuthor") or not report.get("sourceInstitution"))
        and (report_id is None or report.get("id") == report_id)
    ]
    if not targets:
        print("没有需要回填的路演报告。")
        return
    print(f"待回填路演报告 {len(targets)} 篇：")
    for report in targets:
        title = report.get("title", "")
        full = R.report_file_path(report)
        context = ""
        if full:
            context = R._extract_text(full)
        if not context.strip():
            context = f"文件名：{report.get('fileName') or title}"
        prompt = (
            "你是研究报告信息抽取助手。请从下面这份路演报告的标题与内容中识别报告作者与发布机构，"
            '只输出一个JSON对象：{"author": "…", "institution": "…"}\n'
            "规则：\n"
            "1. author：报告作者（封面/署名处的个人或团队，如“东方策略团队”“张三”）；无法确认输出空字符串\n"
            "2. institution：发布机构。标题中“【XX策略】【XX固收】”等团队名往往对应机构；"
            "机构简称换成全称，常见对照："
            + "、".join(f"{k}→{v}" for k, v in R.ROADSHOW_INSTITUTION_ALIASES.items())
            + "；不在对照中的简称按常识补全为全称；无法确认输出空字符串\n"
            "3. 不要把文件上传人当作作者；严格基于内容，不要编造\n\n"
            f"报告标题：{title}\n报告文件名：{report.get('fileName') or ''}\n\n"
            f"报告内容：\n{context[:R.LLM_MAX_TEXT_CHARS]}"
        )
        try:
            parsed = json.loads(R._call_llm(prompt))
        except Exception as exc:
            print(f"  ✗ {title}：识别失败（{exc}），跳过")
            continue
        author = str(parsed.get("author", "")).strip()[:100]
        institution = str(parsed.get("institution", "")).strip()[:120]
        print(f"  {'[dry-run] ' if dry_run else ''}{title}")
        print(f"    作者：{author or '（未识别）'} | 机构：{institution or '（未识别）'}")
        if dry_run:
            continue
        fields = {}
        if author and not report.get("sourceAuthor"):
            fields["sourceAuthor"] = author
        if institution and not report.get("sourceInstitution"):
            fields["sourceInstitution"] = institution
        if fields:
            store.update_report(report["id"], fields)
    if not dry_run:
        print("回填完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只预览识别结果，不写库")
    parser.add_argument("--report-id", default=None, help="只处理指定 id 的报告")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run, report_id=args.report_id)
