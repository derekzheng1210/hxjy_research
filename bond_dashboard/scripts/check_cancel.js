// 量化：DM 中"(取消发行)"状态的券对每日合计的影响
const BASE = "http://localhost:3000/api/dm/primary";
function creditReason(b) {
  const t = `${b.bond_matu_struct ?? ""} ${b.bond_issue_tenor ?? ""} ${b.sec_short_name ?? ""}`;
  if (/\+\s*N/i.test(t) || t.includes("永续")) return "永续";
  const ty = String(b.bond_type_desc ?? "");
  if (ty.includes("PPN")) return "PPN";
  if (ty.includes("可交换")) return "可交换";
  if (ty.includes("可转")) return "可转债";
  if (/^(283|520)/.test(String(b.security_id ?? "")) || String(b.public_offering_status ?? "").includes("私募")) return "私募";
  const _nm = String(b.sec_short_name ?? "");
  if (_nm.includes("转债") || /转\d*$/.test(_nm)) return "可转债";
  return null;
}
function isCancelled(b) {
  const n = String(b.sec_short_name ?? "");
  const s = String(b.issue_status_desc ?? "");
  return n.includes("取消") || s.includes("取消");
}
const sum = (a) => a.reduce((s, b) => s + (b.plan_issue_amount || 0), 0) / 10000;

(async () => {
  for (let d = 1; d <= 7; d++) {
    const ds = "2026-09-" + String(d).padStart(2, "0");
    const r = await fetch(`${BASE}?start=${ds}&end=${ds}&category=1`);
    const j = await r.json();
    const rows = (j.list || []).filter((b) => String(b.subscribe_date ?? "") === ds);
    const clean = rows.filter((b) => !creditReason(b));
    const cancelled = clean.filter(isCancelled);
    const keep = clean.filter((b) => !isCancelled(b));
    console.log(
      ds,
      "clean:", clean.length, "只", sum(clean).toFixed(1), "亿",
      "| 剔除取消后:", keep.length, "只", sum(keep).toFixed(1), "亿",
      "| 取消:", cancelled.length, "只", sum(cancelled).toFixed(1), "亿"
    );
    for (const b of cancelled) console.log("   取消→", b.sec_short_name, (b.plan_issue_amount || 0) / 10000, "亿", b.issue_status_desc);
  }
})();
