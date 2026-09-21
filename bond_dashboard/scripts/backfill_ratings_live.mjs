// 一次性回填：全量拉 DM 评级 → data/cache/issuer_ratings_live.json
// 与 src/lib/dm-refresh.ts 的 refreshIssuerRatings 同口径（解析/合并/文件格式一致），
// 用于服务端首次部署或夜间调度未跑前的手动补齐。用法: node scripts/backfill_ratings_live.mjs [limit]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { sm4 } = require("sm-crypto");

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// 数据目录可外置（与 src/lib/data-dir.ts 同口径：BOND_DASHBOARD_DATA_DIR 优先）
const DATA = process.env.BOND_DASHBOARD_DATA_DIR
  ? path.resolve(process.env.BOND_DASHBOARD_DATA_DIR)
  : path.join(ROOT, "data");
const LIMIT = Number(process.argv[2] ?? 0) || 0;

const env = {};
for (const line of fs.readFileSync(path.join(ROOT, ".env.local"), "utf8").split("\n")) {
  const m = line.match(/^\s*(INNO_APP_KEY|INNO_APP_SECRET)\s*=\s*(.+?)\s*$/);
  if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, "");
}
const key = Buffer.from(env.INNO_APP_SECRET, "utf8").subarray(0, 16);
const b64url = (b) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function postRating(chunk) {
  const hex = sm4.encrypt(JSON.stringify({ comChiNameList: chunk }), key, { mode: "ecb" });
  const res = await fetch("https://gapi-ext.innodealing.com/dm-quant-func-service/api/v1/company/rating/data", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Dm-App-Key": env.INNO_APP_KEY },
    body: b64url(Buffer.from(hex, "hex")),
  });
  const text = await res.text();
  const plain = sm4.decrypt(Buffer.from(JSON.parse(text), "base64").toString("hex"), key, { mode: "ecb" });
  const obj = JSON.parse(plain);
  if (obj.code !== 0) throw new Error(obj.message || obj.code);
  return obj.data ?? [];
}

// ---- 主体清单：issuer_ytd 榜单 ∪ 近 30 日日历主体（与 refreshIssuerRatings 一致）----
const names = new Set();
for (const r of JSON.parse(fs.readFileSync(path.join(DATA, "issuer_ytd.json"), "utf8")).issuers ?? []) {
  const n = String(r.issuer ?? "").trim();
  if (n) names.add(n);
}
const cutoff = new Date(Date.now() - 30 * 86400e3).toISOString().slice(0, 10);
const cal = JSON.parse(fs.readFileSync(path.join(DATA, "excel_calendar.json"), "utf8"));
for (const [d, v] of Object.entries(cal.days ?? {})) {
  if (d < cutoff) continue;
  for (const b of v.bonds ?? []) {
    const n = String(b.issuer ?? "").trim();
    if (n) names.add(n);
  }
}
let list = [...names].sort();
if (LIMIT > 0) list = list.slice(0, LIMIT);
console.log(`待刷新主体：${list.length} 家`);

// ---- 上次 live 值兜底 ----
const livePath = path.join(DATA, "cache", "issuer_ratings_live.json");
let prev = {};
try { prev = JSON.parse(fs.readFileSync(livePath, "utf8")).map ?? {}; } catch {}
const map = {};
for (const n of list) if (prev[n]) map[n] = { ...prev[n] };

// ---- 分批拉取（5 家/批 + 0.35s 限速，429 退避）----
let batches = 0;
const t0 = Date.now();
for (let i = 0; i < list.length; i += 5) {
  const chunk = list.slice(i, i + 5);
  let rows = null;
  for (let attempt = 0; attempt < 3 && !rows; attempt++) {
    try {
      rows = await postRating(chunk);
      batches++;
    } catch (e) {
      if (/429|频繁/.test(String(e)) && attempt < 2) {
        await sleep(1500 * (attempt + 1));
        continue;
      }
      console.log(`  批次 ${i} 失败跳过：${String(e).slice(0, 80)}`);
    }
  }
  const byName = {};
  for (const r of rows ?? []) {
    const name = String(r.comChiName ?? r.com_chi_name ?? "").trim();
    const rating = String(r.rating ?? "").trim();
    if (!name || !rating) continue;
    const date = String(r.ratingDate ?? r.rating_date ?? "");
    const src = String(r.dataSource ?? r.data_source ?? "");
    const cur = byName[name] ?? (byName[name] = {});
    if (src === "YY评分" || (!src && /^[0-9]/.test(rating))) {
      if (!cur.yy || date > (cur.yyDate ?? "")) { cur.yy = rating; cur.yyDate = date; }
    } else if (src === "外部评级" || (!src && /^[A-Za-z]/.test(rating))) {
      if (!cur.external || date > (cur.extDate ?? "")) {
        cur.external = rating;
        cur.extDate = date;
        cur.agency = String(r.ratingInstitutionShortName ?? r.rating_institution_short_name ?? "") || null;
      }
    }
  }
  for (const n of chunk) if (byName[n]) map[n] = { ...map[n], ...byName[n] };
  if ((i / 5) % 50 === 49 || i + 5 >= list.length) {
    console.log(`  ${Math.min(i + 5, list.length)}/${list.length} · YY ${Object.values(map).filter((v) => v.yy).length} · 外部 ${Object.values(map).filter((v) => v.external).length}`);
  }
  await sleep(350);
}

const out = {
  meta: {
    generated: new Date().toISOString(),
    issuerCount: list.length,
    yyCovered: Object.values(map).filter((v) => v.yy).length,
    extCovered: Object.values(map).filter((v) => v.external).length,
    source: "DM company/rating/data（服务器每日自动刷新；读时覆盖 issuer_ytd 内嵌快照）",
  },
  map,
};
fs.mkdirSync(path.dirname(livePath), { recursive: true });
const tmp = livePath + ".tmp";
fs.writeFileSync(tmp, JSON.stringify(out, null, 1), "utf8");
fs.renameSync(tmp, livePath);
console.log(`✓ 写入 ${livePath}`);
console.log(`  主体 ${out.meta.issuerCount} · YY 覆盖 ${out.meta.yyCovered} · 外部评级覆盖 ${out.meta.extCovered} · ${batches} 批 · ${Math.round((Date.now() - t0) / 1000)}s`);
