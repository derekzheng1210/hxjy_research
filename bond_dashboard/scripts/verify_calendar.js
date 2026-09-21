// 临时校验脚本：核对首页「月度窗口拉取 vs 单日拉取」的逐日一致性
// 用法: node verify_calendar.js   （需 localhost:3000 服务在运行）
const BASE = "http://localhost:3000/api/dm/primary";

function cleanReason(b) {
  const perp = `${b.bond_matu_struct ?? ""} ${b.bond_issue_tenor ?? ""} ${b.sec_short_name ?? ""}`;
  if (/\+\s*N/i.test(perp) || perp.includes("永续")) return "永续债";
  const type = String(b.bond_type_desc ?? "");
  if (type.includes("PPN")) return "PPN";
  if (type.includes("可交换")) return "可交换债";
  if (type.includes("可转")) return "可转债";
  const code = String(b.security_id ?? "");
  if (/^(283|520)/.test(code) || String(b.public_offering_status ?? "").includes("私募")) return "私募债";
  const name = String(b.sec_short_name ?? "");
  if (name.includes("转债") || /转\d*$/.test(name)) return "可转债";
  return null;
}

async function getByDay(d) {
  const r = await fetch(`${BASE}?start=${d}&end=${d}&category=1`);
  const j = await r.json();
  const rows = (j.list || []).filter((b) => String(b.subscribe_date ?? "") === d);
  return summarize(d, rows);
}

async function getByMonth(start, end) {
  const r = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start, end, category: 1 }),
  });
  const j = await r.json();
  return j.list || [];
}

function summarize(d, rows) {
  const clean = rows.filter((b) => !cleanReason(b));
  const sum = (arr) => arr.reduce((s, b) => s + (b.plan_issue_amount || 0), 0);
  const ids = (arr) => arr.map((b) => b.security_id).sort().join(",");
  return {
    d,
    rawN: rows.length,
    cleanN: clean.length,
    rawYi: +(sum(rows) / 10000).toFixed(1),
    cleanYi: +(sum(clean) / 10000).toFixed(1),
    ids: ids(clean),
  };
}

(async () => {
  const monthRows = await getByMonth("2026-09-01", "2026-09-30");
  const monthByDay = new Map();
  for (const b of monthRows) {
    const sd = String(b.subscribe_date ?? "");
    if (!sd.startsWith("2026-09")) continue;
    if (!monthByDay.has(sd)) monthByDay.set(sd, []);
    monthByDay.get(sd).push(b);
  }

  let mismatch = 0;
  for (let d = 1; d <= 7; d++) {
    const ds = `2026-09-${String(d).padStart(2, "0")}`;
    const dayRes = await getByDay(ds);
    const monthDay = monthByDay.get(ds) || [];
    const monthRes = summarize(ds, monthDay);
    const sameSet = dayRes.ids === monthRes.ids;
    if (!sameSet || dayRes.cleanYi !== monthRes.cleanYi || dayRes.cleanN !== monthRes.cleanN) mismatch++;
    console.log(
      `${ds} | 单日: raw ${dayRes.rawN}只/clean ${dayRes.cleanN}只/${dayRes.cleanYi}亿 | 月窗: raw ${monthRes.rawN}只/clean ${monthRes.cleanN}只/${monthRes.cleanYi}亿 | 集合一致=${sameSet}`
    );
    // 差异明细
    if (!sameSet) {
      const dayIds = new Set(dayRes.ids.split(","));
      const monthIds = new Set(monthRes.ids.split(","));
      const onlyDay = [...dayIds].filter((x) => x && !monthIds.has(x));
      const onlyMonth = [...monthIds].filter((x) => x && !dayIds.has(x));
      if (onlyDay.length) console.log(`   ↑ 单日有、月窗缺(${onlyDay.length}):`, onlyDay.slice(0, 8).join(" "));
      if (onlyMonth.length) console.log(`   ↑ 月窗有、单日缺(${onlyMonth.length}):`, onlyMonth.slice(0, 8).join(" "));
    }
  }
  console.log(mismatch === 0 ? "✅ 9/1-9/7 单日与月窗口结果完全一致" : `❌ 有 ${mismatch} 天不一致`);
})();
