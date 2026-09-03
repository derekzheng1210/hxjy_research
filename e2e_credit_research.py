# -*- coding: utf-8 -*-
"""端到端验证：登录门户 → 空态 → 启动真实 Agent 生成 → 轮询进度 → 缓存 → 人工覆盖。"""
import json
import sys
import time

import requests

BASE = "http://127.0.0.1:5011"
CODE = "102682773.IB"
MODE = sys.argv[1] if len(sys.argv) > 1 else "full"

s = requests.Session()
r = s.post(f"{BASE}/login", data={"password": "Abcd123%"}, allow_redirects=False, timeout=15)
assert r.status_code in (301, 302), f"登录失败: {r.status_code}"
print("[1] 门户登录 OK")

url = f"{BASE}/api/bond-credit-research/{CODE}"
r = s.get(url, timeout=30)
initial_generated = (r.json().get("meta") or {}).get("generated_at")
print(f"[2] 前置 GET: 已有报告={bool(initial_generated)} ({initial_generated}), agent_configured={r.json().get('agent_configured')}")

r = s.post(url, json={"force": True, "mode": MODE}, timeout=30)
job = r.json()
print(f"[3] 启动/复用任务 {r.status_code}: job_id={job.get('job_id')}, mode={job.get('mode', MODE)}")
assert r.status_code == 200 and job.get("job_id"), job

stages_seen, last, t0 = set(), None, time.time()
final = None
progresses = []
while time.time() - t0 < 900:
    time.sleep(4)
    r = s.get(url, timeout=30)
    d = r.json()
    running = d.get("running") or {}
    state = running.get("state")
    if state == "failed":
        print(f"[4] 失败: {running.get('error')}")
        sys.exit(1)
    if running.get("progress") is not None and running.get("stage") in ("queued", "juyuan", "agent", "conclude", "validate"):
        progresses.append((running.get("stage"), round(running.get("progress"), 1)))
    done_now = (not state or state == "done") and d.get("report") and (
        MODE != "full" and (d.get("meta") or {}).get("sections", {}).get(MODE)
        or MODE == "full" and (d.get("meta") or {}).get("generated_at") != initial_generated
    )
    if done_now:
        final = d
        break
    key = (state, running.get("stage"), running.get("detail"))
    if key != last:
        stages_seen.add(running.get("stage"))
        print(f"    [{time.time()-t0:5.1f}s] {running.get('stage_text')} | {running.get('detail')} | {running.get('progress')}%")
        last = key
assert final and final.get("report"), "超时未完成"

# 校验进度单调不回落
lowest_then_higher = False
floor = 0.0
for stage, p in progresses:
    if p < floor - 0.01:
        lowest_then_higher = True
    floor = max(floor, p)
print(f"    进度序列: {progresses}")
assert not lowest_then_higher, "进度出现回落"

meta = final.get("meta") or {}
report = final["report"]
print(f"[4] 完成，用时 {time.time()-t0:.0f}s；channel={meta.get('channel')}, model={meta.get('model')}, plugin_calls={meta.get('plugin_calls')}, mode={MODE}")
print(f"    sections: {meta.get('sections')}")
print(f"    分类: {report['classification']['type']} ({report['classification']['confidence']}) 聚源标志={report['classification']['juyuan_city_flag']}")
print(f"    简介({len(report['brief']['company_intro'])}字): {report['brief']['company_intro'][:60]}…")
print(f"    一句话({len(report['brief']['one_sentence_conclusion'])}字): {report['brief']['one_sentence_conclusion']}")
print(f"    变化{len(report['brief']['key_changes'])}条 风险{len(report['brief']['top_risks'])}项 舆情={report['brief']['public_opinion']['verdict']} 来源={len(report['sources'])}条")
print(f"    校验警告: {meta.get('validation', {}).get('warnings')}")

if MODE == "opinion":
    events = report["details"].get("public_opinion_events") or []
    print(f"    舆情事件{len(events)}条:")
    for e in events:
        print(f"      - [{e.get('level')}] {e.get('date')} {str(e.get('title'))[:40]}")

r = s.get(url, timeout=30)
d = r.json()
print(f"[5] 缓存 GET: report={'report' in d}, running={bool(d.get('running'))}, generated_at={d.get('meta', {}).get('generated_at')}")

if MODE == "full":
    r = s.post(url + "/override", json={"type": "CITY_HYBRID", "note": "E2E测试覆盖"}, timeout=30)
    d = r.json()
    print(f"[6] 人工覆盖 {r.status_code}: type={d.get('report', {}).get('classification', {}).get('type')}, "
          f"auto={d.get('report', {}).get('classification', {}).get('auto_type')}, at={d.get('override_updated_at')}")
print("E2E 全部通过")
