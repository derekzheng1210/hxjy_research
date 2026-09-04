# -*- coding: utf-8 -*-
"""诊断supaw Agent流式事件形态：转储全部SSE data负载并做统计。

用法（在项目根目录）：
    python -X utf8 scripts/diag_agent_stream.py [提示词]

默认提示词很短，只用于观察 message/text/reasoning 事件的id类型、delta标记、
content字段等形态，不产生研究任务。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bond_detail.credit_research import (  # noqa: E402
    _agent_session,
    get_agent_token,
)
from bond_detail.credit_research import AGENT_BASE_URL  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "scripts" / "diag_sse_dump.txt"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    prompt = (sys.argv[1] if len(sys.argv) > 1 else
              "请查询债券102400738.IB的发行人全称和最新主体评级，用两句话回答。")
    body = {
        "input": [{"role": "user", "type": "message",
                   "content": [{"type": "text", "text": prompt, "status": "created"}]}],
        "session_id": "diag",
        "user_id": "diag",
        "channel": "console",
        "stream": True,
    }
    session = _agent_session()
    resp = session.post(
        AGENT_BASE_URL + "/api/aipa/v1/supaw/chat/stream",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Authorization": f"Bearer {get_agent_token()}",
        },
        stream=True,
        timeout=(15, 120),
    )
    print("HTTP", resp.status_code)
    lines = []
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line.startswith("data:"):
            lines.append(line[5:].strip())
    resp.close()
    OUT.write_text("\n\n".join(lines), encoding="utf-8")
    print(f"dumped {len(lines)} data payloads -> {OUT}")

    # 统计事件形态
    kinds = Counter()
    id_types = Counter()
    samples: dict[str, list] = {}
    for payload in lines:
        try:
            outer = json.loads(payload)
        except ValueError:
            kinds["<outer-unparsable>"] += 1
            continue
        if outer.get("type") == "complete":
            kinds["<outer-complete>"] += 1
            continue
        try:
            inner = json.loads(outer.get("data") or "{}")
        except (ValueError, TypeError):
            kinds["<inner-unparsable>"] += 1
            continue
        kind = inner.get("type")
        kinds[kind] += 1
        for field in ("id", "msg_id"):
            if field in inner:
                id_types[f"{kind}.{field}:{type(inner[field]).__name__}"] += 1
        if kind in ("message", "text", "reasoning") and len(samples.get(kind, [])) < 6:
            samples.setdefault(kind, []).append(inner)
    print("kinds:", dict(kinds))
    print("id types:", dict(id_types))
    for kind, items in samples.items():
        print(f"--- {kind} samples ---")
        for item in items:
            print(" ", json.dumps(item, ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
