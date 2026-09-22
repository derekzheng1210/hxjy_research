// DM 每日自动刷新（服务器内建调度，不依赖打包者本机的 daily_refresh.py）
// 职责（只做服务器自己能产出的部分，与 Excel 上传/同事机器管道的数据主权互不覆盖）：
//  1. 日历补数：DM 拉近 N 日一级发行 → 填充 excel_calendar.json 中「没有 Excel 上传」的交易日
//     （Excel 上传过的日期为权威，DM 不动）；顺带回填 recommended.json 的实际发行规模/缺失票面
//  2. 个券估值：DM market-data/date + basic-info 重抓 valuations.json（参与/中标 + 推荐个券）
//  3. 30Y 国债：DM 收益率曲线增量追加 gov30y.json
//  4. 发行人评级：DM company/rating/data 全榜单刷新 → cache/issuer_ratings_live.json
//     （issuer 页「外部评级/YY」的 issuer_ytd.json 是同事机器管道快照、重建不及时，
//      且其 sync_issuer_ratings.py 对已有评级的主体永不重查；live 文件每晚覆盖刷新，
//      读时以 live 优先、快照兜底，评级变动 T+1 生效）
//  5. 参与/中标记录回填：DM basic-info 补缴款日/上市日，缺代码按简称反查
//     （sync_list_dates.py 依赖打包者机器 Python 环境，服务器上跑不了 → TS 移植）
//  6. 票面曲线：DM 一级发行 YTD 全量重建 coupon_curve.json（build_coupon_curve.py 的 TS 移植——
//      本机无 raw_primary_history.csv，曲线此前停在打包日无法更新）
//  7. 发行人分析 YTD：DM 一级发行 YTD 全量重建 issuer_ytd.json + issuer_bonds.json
//     （build_issuer_ytd.py 的 TS 移植——issuer_ytd 原是打包者机器管道快照，停在打包日后
//      新发主体在「发行人分析」页搜不到；内嵌评级取当晚 live，读时 live 仍优先覆盖）
// 调度：src/instrumentation.ts 启动时注册，每日 DM_REFRESH_AT（默认 19:00）后自动跑一次；
//      手动触发走 POST /api/refresh。状态落 data/refresh_state.json。
import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { fetchPrimaryAll, fetchYieldCurve, fetchBasicInfo, fetchCompanyRatingHistory, postData } from "./dm-client";
import { cleanReason, isPPN } from "./credit";
import { getCompanyRatings, localYyOf } from "./ratings";
import { getBids, updateBid } from "./store";
import type { BidRecord, ExcelBond } from "./types";

const MARKET_DATE = "/dm-quant-func-service/api/v1/bond/market-data/date";
const STATE_FILE = path.join(DATA_DIR, "refresh_state.json");

// ==================== 小工具 ====================

function readJson<T>(rel: string, fallback: T): T {
  try {
    const p = path.join(DATA_DIR, rel);
    if (!fs.existsSync(p)) return fallback;
    return JSON.parse(fs.readFileSync(p, "utf8")) as T;
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(absPath: string, data: unknown) {
  fs.mkdirSync(path.dirname(absPath), { recursive: true });
  const tmp = absPath + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data, null, 1), "utf8");
  fs.renameSync(tmp, absPath);
}

function todayStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function addDays(date: string, n: number): string {
  const d = new Date(date + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function prevWorkday(): string {
  const d = new Date(todayStr() + "T12:00:00");
  do {
    d.setDate(d.getDate() - 1);
  } while (d.getDay() === 0 || d.getDay() === 6);
  return d.toISOString().slice(0, 10);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** 代码清洗为 DM 标准格式（8 位纯数字→.IB，6 位→.SH；已有后缀不动）——与 fetch_valuations.py 一致 */
function cleanCode(c: string | null | undefined): string | null {
  if (!c) return null;
  const s = String(c).trim();
  if (/\.(IB|SH|SZ)$/.test(s)) return s;
  if (/^\d+$/.test(s)) return s.length >= 8 ? s + ".IB" : s + ".SH";
  return s;
}

// ==================== 1. 日历补数（DM → excel_calendar.json） ====================

interface CalFile {
  meta: { source?: string; generated?: string; fileCount?: number };
  days: Record<string, { file: string; count: number; planYi: number; bonds: ExcelBond[] }>;
}

interface DmRow {
  sec_short_name?: string | null;
  security_id?: string | null;
  subscribe_date?: string | null;
  bond_type_desc?: string | null;
  bond_issue_tenor?: string | null;
  issuer_full_name?: string | null;
  issuer_yy?: string | null;
  plan_issue_amount?: number | null;
  actu_issue_amount?: number | null;
  issue_yield?: number | null;
  issue_price_forecast?: number | null;
  pay_date?: string | null;
  [k: string]: unknown;
}

async function refreshCalendar(days: number) {
  const cal = readJson<CalFile>("excel_calendar.json", { meta: {}, days: {} });
  const end = todayStr();
  const start = addDays(end, -(days - 1));

  const rows = (await fetchPrimaryAll(start, end, 1)) as unknown as DmRow[];
  const byDate = new Map<string, DmRow[]>();
  for (const r of rows) {
    const d = String(r.subscribe_date ?? "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) continue;
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d)!.push(r);
  }

  // YY 兜底：DM 无 YY 的主体 → company/rating 接口（带缓存+当日哨兵）→ 本地映射（yy_lookup + issuer_ratings）
  const needRating = new Set<string>();
  for (const list of byDate.values()) {
    for (const b of list) {
      const issuer = String(b.issuer_full_name ?? "").trim();
      if (issuer && !b.issuer_yy && !localYyOf(issuer)) needRating.add(issuer);
    }
  }
  const ratingMap = needRating.size ? await getCompanyRatings([...needRating]) : {};

  let daysAdded = 0;
  let bondsAdded = 0;
  for (const [d, list] of byDate) {
    if (cal.days[d]) continue; // Excel 上传过的日期为权威，DM 不覆盖
    const dayRows = list.filter((b) => !isPPN(b) && b.sec_short_name);
    if (!dayRows.length) continue;
    const bonds: ExcelBond[] = dayRows.map((b) => {
      const issuer = String(b.issuer_full_name ?? "").trim();
      return {
        name: String(b.sec_short_name),
        amountYi: b.plan_issue_amount ? Math.round((b.plan_issue_amount / 10000) * 100) / 100 : 0,
        type: b.bond_type_desc ?? null,
        tenor: b.bond_issue_tenor ?? null,
        issuer: issuer || null,
        yy: (b.issuer_yy as string | null) ?? ratingMap[issuer]?.yy ?? localYyOf(issuer) ?? null,
        forecast: b.issue_price_forecast ?? null,
        coupon: b.issue_yield ?? null,
        payDate: b.pay_date ?? null,
        recommended: false,
      };
    });
    cal.days[d] = {
      file: "DM 自动同步",
      count: bonds.length,
      planYi: Math.round(bonds.reduce((s, b) => s + (b.amountYi || 0), 0) * 100) / 100,
      bonds,
    };
    daysAdded++;
    bondsAdded += bonds.length;
  }
  if (daysAdded) {
    cal.meta.generated = new Date().toISOString();
    cal.meta.fileCount = Object.keys(cal.days).length;
    writeJsonAtomic(path.join(DATA_DIR, "excel_calendar.json"), cal);
  }

  // recommended.json 回填：窗口期内推荐个券的实际发行规模 / 缺失票面 / 缺失缴款日 / 缺失代码
  // （DM 为实际值的权威来源；只填空与修正 actual，不覆盖 Excel 人工标注）
  const rec = readJson<Record<string, unknown>[]>("recommended.json", []);
  const dmByName = new Map<string, DmRow>();
  for (const [d, list] of byDate) {
    for (const b of list) dmByName.set(`${d}|${b.sec_short_name}`, b);
  }
  let recBackfilled = 0;
  const recPatched = rec.map((x) => {
    const key = `${String(x.date ?? "")}|${String(x.name ?? "")}`;
    const b = dmByName.get(key);
    if (!b) return x;
    const patch: Record<string, unknown> = {};
    const act = b.actu_issue_amount ? Math.round((b.actu_issue_amount / 10000) * 100) / 100 : null;
    if (act && act !== x.actual) patch.actual = act;
    if ((x.coupon === null || x.coupon === undefined) && b.issue_yield != null) patch.coupon = b.issue_yield;
    if (!x.pay_date && b.pay_date) patch.pay_date = b.pay_date;
    if (!x.code && b.security_id) patch.code = b.security_id;
    if (Object.keys(patch).length) {
      recBackfilled++;
      return { ...x, ...patch };
    }
    return x;
  });
  if (recBackfilled) writeJsonAtomic(path.join(DATA_DIR, "recommended.json"), recPatched);

  return { daysAdded, bondsAdded, recBackfilled, window: `${start}~${end}` };
}

// ==================== 2. 个券估值（DM → valuations.json，fetch_valuations.py 移植） ====================

interface ValBond {
  security_id: string | null;
  cb: number | null;
  cs: number | null;
  cb_basis: string | null;
  cs_basis: string | null;
  market: string | null;
}

function pick(ytm: unknown, yte: unknown): [number | null, string | null] {
  if (yte !== null && yte !== undefined && String(yte).trim() !== "" && !Number.isNaN(Number(yte))) return [Number(yte), "行权"];
  if (ytm !== null && ytm !== undefined && String(ytm).trim() !== "" && !Number.isNaN(Number(ytm))) return [Number(ytm), "到期"];
  return [null, null];
}

async function refreshValuations() {
  // 收集个券：投标记录（SQLite 权威）+ 推荐个券
  const bonds = new Map<string, { security_id: string | null }>();
  for (const b of getBids()) {
    if (!b.bondName) continue;
    if (!bonds.has(b.bondName)) bonds.set(b.bondName, { security_id: cleanCode(b.securityId) });
    else if (!bonds.get(b.bondName)!.security_id) bonds.get(b.bondName)!.security_id = cleanCode(b.securityId);
  }
  for (const r of readJson<Record<string, unknown>[]>("recommended.json", [])) {
    const name = String(r.name ?? "");
    if (!name) continue;
    if (!bonds.has(name)) bonds.set(name, { security_id: cleanCode(r.code as string | null) });
    else if (!bonds.get(name)!.security_id) bonds.get(name)!.security_id = cleanCode(r.code as string | null);
  }
  const ids = [...new Set([...bonds.values()].map((b) => b.security_id).filter((x): x is string => !!x))];
  if (!ids.length) return { bonds: 0, valuedDate: "", haveCb: 0, haveCs: 0 };

  // ① 发行场所（basic-info，每批 20）
  const marketMap = new Map<string, { market: string; cros: string }>();
  for (let i = 0; i < ids.length; i += 20) {
    const chunk = ids.slice(i, i + 20);
    try {
      const rows = (await fetchBasicInfo(chunk)) as Array<Record<string, unknown>>;
      for (const r of rows) {
        const sid = String(r.security_id ?? "");
        if (sid) marketMap.set(sid, { market: String(r.secondary_market ?? ""), cros: String(r.is_cros_mar ?? "") });
      }
    } catch {
      /* 单批失败跳过 */
    }
    await sleep(150);
  }

  // ② 估值（market-data/date，每批 5；当日覆盖率 <30% 视为未发布，回退上一工作日）
  const fetchVals = async (date: string) => {
    const valMap = new Map<string, Record<string, unknown>>();
    for (let i = 0; i < ids.length; i += 5) {
      const chunk = ids.slice(i, i + 5);
      try {
        const resp = await postData<Record<string, unknown>[] | { list?: unknown[] }>(
          { securityIdList: chunk, dataSourceList: [2], startDate: date, endDate: date },
          MARKET_DATE
        );
        const rows = (Array.isArray(resp) ? resp : (resp?.list as Record<string, unknown>[]) ?? []) as Record<string, unknown>[];
        for (const r of rows) {
          const sid = String(r.security_id ?? "");
          if (sid) valMap.set(sid, r);
        }
      } catch {
        /* 单批失败跳过 */
      }
      await sleep(120);
    }
    return valMap;
  };
  let date = todayStr();
  let valMap = await fetchVals(date);
  if (valMap.size < 0.3 * ids.length) {
    const fb = prevWorkday();
    date = fb;
    valMap = await fetchVals(fb);
  }

  // ③ 按场所规则提取（含权一律行权；银行间取中债、交易所取中证、双市场都取）
  const out: Record<string, ValBond> = {};
  for (const [name, b] of bonds) {
    if (!b.security_id) {
      out[name] = { security_id: null, cb: null, cs: null, cb_basis: null, cs_basis: null, market: null };
      continue;
    }
    const info = marketMap.get(b.security_id);
    const market = info?.market ?? "";
    const cros = info?.cros ?? "";
    const v = valMap.get(b.security_id) ?? {};
    const [cb, cbBasis] = pick(v.cb_ytm, v.cb_yte);
    const [cs, csBasis] = pick(v.cs_ytm, v.cs_yte);
    const isInterbank = market.includes("银行间");
    const isExchange = market.includes("交易所") || market.includes("证券交易");
    const isCros = cros.includes("是");
    if (isCros || (isInterbank && isExchange)) {
      out[name] = { security_id: b.security_id, cb, cs, cb_basis: cbBasis, cs_basis: csBasis, market: market || "双市场" };
    } else if (isInterbank) {
      out[name] = { security_id: b.security_id, cb, cs: null, cb_basis: cbBasis, cs_basis: null, market };
    } else if (isExchange) {
      out[name] = { security_id: b.security_id, cb: null, cs, cb_basis: null, cs_basis: csBasis, market };
    } else {
      out[name] = { security_id: b.security_id, cb, cs, cb_basis: cbBasis, cs_basis: csBasis, market: market || "未知" };
    }
  }

  writeJsonAtomic(path.join(DATA_DIR, "valuations.json"), {
    meta: {
      date,
      generated: new Date().toISOString(),
      source: "DM market-data/date + basic-info/info（服务器每日自动刷新）",
      valuation_rule: "含权债取行权收益率(yte)，非含权取到期收益率(ytm)",
    },
    bonds: out,
  });
  return {
    bonds: out.size,
    valuedDate: date,
    haveCb: Object.values(out).filter((v) => v.cb !== null).length,
    haveCs: Object.values(out).filter((v) => v.cs !== null).length,
  };
}

// ==================== 3. 30Y 国债收益率（DM → gov30y.json） ====================

async function refreshGov30y() {
  const file = readJson<{ meta: { source?: string; updated?: string }; map: Record<string, number> }>("gov30y.json", {
    meta: {},
    map: {},
  });
  const end = todayStr();
  const start = addDays(end, -30); // 近 30 天增量（覆盖节假日缺口）
  let added = 0;
  // DM 曲线接口对日期跨度有限制：按 ≤12 天分段拉取（与 build_portfolio_series.py 一致）
  for (let s = start; s <= end; s = addDays(s, 12)) {
    const e = s === end ? end : [addDays(s, 11), end].sort()[0];
    let rows: Array<Record<string, unknown>> = [];
    try {
      rows = (await fetchYieldCurve(s, e, "中债国债收益率曲线", ["30"], "1")) as Array<Record<string, unknown>>;
    } catch {
      /* 单段失败跳过，继续下一段 */
    }
    for (const r of rows) {
      const d = String(r.valuation_date ?? "");
      const y = r.yield;
      if (/^\d{4}-\d{2}-\d{2}$/.test(d) && y !== null && y !== undefined && !Number.isNaN(Number(y))) {
        if (file.map[d] !== Number(y)) added++;
        file.map[d] = Number(y);
      }
    }
    await sleep(200);
  }
  if (added) {
    file.meta = { source: "DM 中债国债收益率曲线 30Y（服务器每日自动刷新）", updated: new Date().toISOString() };
    writeJsonAtomic(path.join(DATA_DIR, "gov30y.json"), file);
  }
  return { points: Object.keys(file.map).length, added, window: `${start}~${end}` };
}

// ==================== 4. 发行人评级（DM → cache/issuer_ratings_live.json） ====================
// issuer 页「外部评级/YY」的 issuer_ytd.json 虽已由第 7 步每晚重建，但评级日内变动要等到
// 19:00 重建才生效；此处每晚拉 DM company/rating/data（YY评分 + 外部评级各取 rating_date 最新一条）
// 覆盖式刷新，读时 live 优先。写独立 live 文件而非管道的 issuer_ratings.json：互不覆盖。

interface LiveRatingEntry {
  yy?: string | null; // YY 评分（1~8 档位，DM data_source=YY评分 最新行）
  yyDate?: string | null;
  external?: string | null; // 外部评级（AAA/AA+，DM data_source=外部评级 最新行）
  extDate?: string | null;
  agency?: string | null;
}

interface RatingsLiveFile {
  meta: { generated: string; issuerCount: number; yyCovered: number; extCovered: number; source: string };
  map: Record<string, LiveRatingEntry>;
}

/** 收集需刷新评级的主体：issuer_ytd 榜单 ∪ 近 30 日日历出现过的主体（新主体在管道重建前也能被刷到） */
function collectRatingIssuers(): string[] {
  const names = new Set<string>();
  const ytd = readJson<{ issuers?: Array<{ issuer?: string | null }> }>("issuer_ytd.json", { issuers: [] });
  for (const r of ytd.issuers ?? []) {
    const n = String(r.issuer ?? "").trim();
    if (n) names.add(n);
  }
  const cal = readJson<CalFile>("excel_calendar.json", { meta: {}, days: {} });
  const cutoff = addDays(todayStr(), -30);
  for (const [d, v] of Object.entries(cal.days)) {
    if (d < cutoff) continue;
    for (const b of v.bonds ?? []) {
      const n = String(b.issuer ?? "").trim();
      if (n) names.add(n);
    }
  }
  return [...names].sort();
}

export async function refreshIssuerRatings(limit = 0) {
  const t0 = Date.now();
  let list = collectRatingIssuers();
  if (limit > 0) list = list.slice(0, limit);
  if (!list.length) return { issuers: 0, yy: 0, ext: 0, batches: 0, ms: 0 };

  // 上次 live 值兜底：本轮 DM 查失败/无评级行的主体保留旧值，读时非空才覆盖快照
  const prev = readJson<{ map?: Record<string, LiveRatingEntry> }>("cache/issuer_ratings_live.json", { map: {} }).map ?? {};
  const map: Record<string, LiveRatingEntry> = {};
  for (const n of list) {
    const p = prev[n];
    if (p) map[n] = { ...p };
  }

  let batches = 0;
  for (let i = 0; i < list.length; i += 5) {
    const chunk = list.slice(i, i + 5);
    try {
      const rows = (await fetchCompanyRatingHistory(chunk)) as Array<{
        com_chi_name?: string | null;
        data_source?: string | null;
        rating?: string | null;
        rating_date?: string | null;
        rating_institution_short_name?: string | null;
      }>;
      batches++;
      const byName: Record<string, LiveRatingEntry> = {};
      for (const r of rows) {
        const name = String(r.com_chi_name ?? "").trim();
        const rating = String(r.rating ?? "").trim();
        if (!name || !rating) continue;
        const date = String(r.rating_date ?? "");
        const src = String(r.data_source ?? "");
        const cur = byName[name] ?? (byName[name] = {});
        // 口径对齐 sync_issuer_ratings.py：YY评分=数字档位（1~8），外部评级=字母（AAA/AA+）
        if (src === "YY评分" || (!src && /^[0-9]/.test(rating))) {
          if (!cur.yy || date > (cur.yyDate ?? "")) {
            cur.yy = rating;
            cur.yyDate = date;
          }
        } else if (src === "外部评级" || (!src && /^[A-Za-z]/.test(rating))) {
          if (!cur.external || date > (cur.extDate ?? "")) {
            cur.external = rating;
            cur.extDate = date;
            cur.agency = String(r.rating_institution_short_name ?? "") || null;
          }
        }
      }
      for (const n of chunk) {
        const f = byName[n];
        if (f) map[n] = { ...map[n], ...f };
      }
    } catch {
      /* 单批失败跳过（保留上次值），明日重试 */
    }
    await sleep(350); // company/rating 单次≤5 主体，限速与同事脚本一致
  }

  const yy = Object.values(map).filter((v) => v.yy).length;
  const ext = Object.values(map).filter((v) => v.external).length;
  writeJsonAtomic(path.join(DATA_DIR, "cache", "issuer_ratings_live.json"), {
    meta: {
      generated: new Date().toISOString(),
      issuerCount: list.length,
      yyCovered: yy,
      extCovered: ext,
      source: "DM company/rating/data（服务器每日自动刷新；读时覆盖 issuer_ytd 内嵌快照）",
    },
    map,
  } satisfies RatingsLiveFile);
  return { issuers: list.length, yy, ext, batches, ms: Date.now() - t0 };
}

// ==================== 5. 参与/中标记录回填（缴款日/上市日/代码/发行人/票面） ====================
// scripts/sync_list_dates.py 的 TS 移植（该脚本依赖打包者机器的 Python 环境路径，部署机上无法运行）：
// - 缺 securityId 的记录先按简称反查：data/store/name_code.json → DM 一级发行（投标日 ±10 日窗口）
// - 再按 basic-info 回填：缴款日=inte_start_date（起息日=缴款日，缺则退一级发行 pay_date）、
//   上市日=list_date（DM 为准，替换估算值）、发行人=issuer_name、票面=iss_coup_rate（仅填空）
// 上市提醒板块对缺缴款日/上市日的记录只能显示「待补充」，故本步每晚执行。

function normKeep(s: string): string {
  return (s || "").replace(/[（）()\s]/g, "");
}

function normDrop(s: string): string {
  return (s || "").replace(/[（(][^）)]*[）)]/g, "").replace(/\s+/g, "");
}

function date10(v: unknown): string | null {
  const s = String(v ?? "").trim();
  return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : null;
}

export async function refreshBidsInfo() {
  const all = getBids().filter((b) => b.type === "won" || b.type === "participated");
  const targets = all.filter((b) => !b.securityId || !b.payDate || !b.listDate || !b.issuer);
  if (!targets.length) return { checked: 0, patched: 0, code: 0, payDate: 0, listDate: 0 };

  // ① 简称 → 代码：本地映射三档匹配（原名 / 去括号保留内容 / 连括号内容去掉）
  const nameCode = readJson<Record<string, string>>("store/name_code.json", {});
  const codeOf = new Map<BidRecord, string>();
  const payFallback = new Map<BidRecord, string>();
  const unresolved: BidRecord[] = [];
  for (const b of targets) {
    if (b.securityId && !b.securityId.startsWith("DY")) {
      codeOf.set(b, b.securityId);
      continue;
    }
    const name = b.bondName.trim();
    let hit = "";
    for (const key of [name, normKeep(name), normDrop(name)]) {
      if (key && nameCode[key]) {
        hit = nameCode[key];
        break;
      }
    }
    if (hit) codeOf.set(b, hit);
    else unresolved.push(b);
  }

  // ② 仍无代码：按投标日 ±10 日窗口拉 DM 一级发行，按简称反查 securityId（顺带记 pay_date 兜底）
  if (unresolved.length) {
    const win = new Map<string, DmRow>();
    const doneDates = new Set<string>();
    for (const b of unresolved) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(b.bidDate) || doneDates.has(b.bidDate)) continue;
      doneDates.add(b.bidDate);
      try {
        const rows = (await fetchPrimaryAll(addDays(b.bidDate, -10), addDays(b.bidDate, 10), 1)) as unknown as DmRow[];
        for (const r of rows) {
          const sid = String(r.security_id ?? "").trim();
          const nm = String(r.sec_short_name ?? "").trim();
          if (!sid || sid.startsWith("DY") || !nm) continue;
          for (const k of new Set([nm, normKeep(nm), normDrop(nm)])) {
            if (!win.has(k)) win.set(k, r);
          }
        }
      } catch {
        /* 单窗口失败跳过 */
      }
      await sleep(200);
    }
    for (const b of unresolved) {
      const name = b.bondName.trim();
      for (const k of [name, normKeep(name), normDrop(name)]) {
        const r = k ? win.get(k) : undefined;
        const sid = String(r?.security_id ?? "").trim();
        if (r && sid && !sid.startsWith("DY")) {
          codeOf.set(b, sid);
          const pd = date10(r.pay_date);
          if (pd) payFallback.set(b, pd);
          break;
        }
      }
    }
  }

  // ③ basic-info 批量查（每批 20）：上市日/缴款日/发行人/票面
  const codes = [...new Set([...codeOf.values()])];
  const infoMap = new Map<string, Record<string, unknown>>();
  for (let i = 0; i < codes.length; i += 20) {
    try {
      const rows = (await fetchBasicInfo(codes.slice(i, i + 20))) as Array<Record<string, unknown>>;
      for (const r of rows) {
        const sid = String(r.security_id ?? "").trim();
        if (sid) infoMap.set(sid, r);
      }
    } catch {
      /* 单批失败跳过 */
    }
    await sleep(150);
  }

  let patched = 0;
  let nCode = 0;
  let nPay = 0;
  let nList = 0;
  for (const b of targets) {
    const code = codeOf.get(b);
    const info = code ? infoMap.get(code) : undefined;
    const patch: Partial<BidRecord> = {};
    if (!b.securityId && code) {
      patch.securityId = code;
      nCode++;
    }
    if (!b.payDate) {
      const pay = date10(info?.inte_start_date) ?? payFallback.get(b) ?? null;
      if (pay) {
        patch.payDate = pay;
        nPay++;
      }
    }
    // 上市日以 DM 为准（人工/估算值被精确值替换，与 sync_list_dates.py 口径一致）
    const ld = date10(info?.list_date);
    if (ld && b.listDate !== ld) {
      patch.listDate = ld;
      nList++;
    }
    if (!b.issuer && info?.issuer_name) patch.issuer = String(info.issuer_name);
    if (b.coupon == null && info?.iss_coup_rate != null) {
      const c = Number(info.iss_coup_rate);
      if (Number.isFinite(c) && c > 0) patch.coupon = c;
    }
    if (Object.keys(patch).length) {
      updateBid(b.id, patch);
      patched++;
    }
  }
  return { checked: targets.length, patched, code: nCode, payDate: nPay, listDate: nList };
}

// ==================== 6. 信用债票面-期限曲线（DM → coupon_curve.json） ====================
// scripts/build_coupon_curve.py 的 TS 移植。原脚本读打包者机器的 raw_primary_history.csv，
// 部署机上不存在 → 曲线停在打包日。此处从 DM 按月窗口拉 YTD 一级发行全量重建（幂等覆盖写）。
// 口径与 Python 版一致：信用债、剔 PPN/定向/私募/转债/可交换/永续/无票面、行权前期限≤30.5 年、
// YY 1-5 档（yy_lookup 优先 → DM 评级缓存 yy_issuer/getCompanyRatings 兜底）、
// 票面按实际发行金额加权、档位 ≤1Y/2Y/…/30Y（档间取关键期限中点切分）。

const CURVE_START = "2026-01-01";
const CURVE_BUCKETS: Array<[string, number, number]> = [
  ["≤1Y", 0, 1.5],
  ["2Y", 1.5, 2.5],
  ["3Y", 2.5, 4],
  ["5Y", 4, 6],
  ["7Y", 6, 8.5],
  ["10Y", 8.5, 12.5],
  ["15Y", 12.5, 17.5],
  ["20Y", 17.5, 25],
  // 30Y 上界 30.5 容差：DM 30 年期 mat 常记 30.02Y（计息天数折算），须归入 30Y 档
  ["30Y", 25, 30.5],
];

function parseYears(s: string | null | undefined): number | null {
  const t = (s ?? "").trim();
  let m = /^([0-9]+(?:\.[0-9]+)?)\s*Y/i.exec(t);
  if (m) return parseFloat(m[1]);
  m = /^([0-9]+)\s*D/i.exec(t);
  if (m) return parseInt(m[1], 10) / 365.0;
  m = /^([0-9]+(?:\.[0-9]+)?)\s*年/.exec(t);
  if (m) return parseFloat(m[1]);
  return null;
}

/** 行权前期限：含权债取括号内首段（5Y(3+2)→3、4Y(2+2)→2），非含权取原期限 */
function preTenor(mat: string | null | undefined, tenor: string | null | undefined): number | null {
  const s = (mat ?? "").trim() || (tenor ?? "").trim();
  const m = /\((.*?)\)/.exec(s);
  if (m) {
    const part = m[1].trim().split("+")[0].trim();
    let v = parseYears(part);
    if (v === null) {
      const f = parseFloat(part);
      if (Number.isFinite(f)) v = f;
    }
    if (v !== null) return v;
  }
  return parseYears(s);
}

function bucketOf(t: number): string | null {
  for (const [name, lo, hi] of CURVE_BUCKETS) if (t > lo && t <= hi) return name;
  return null;
}

/** YY 评分 1-5 档（含子档 1-/1/1+…5+） */
function yyInScope(yy: string): boolean {
  const m = /^\s*([0-9]+)/.exec(yy);
  return !!m && [1, 2, 3, 4, 5].includes(parseInt(m[1], 10));
}

const round2 = (v: number) => Math.round(v * 100) / 100;
const round3 = (v: number) => Math.round(v * 1000) / 1000;
const round4 = (v: number) => Math.round(v * 10000) / 10000;

function localStamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

interface CurveRow {
  name: string;
  date: string;
  issuer: string;
  yy: string;
  termY: number;
  coupon: number;
  amtYi: number;
  planAmtYi: number;
  actuAmtYi: number;
  bucket: string;
}

export async function refreshCouponCurve() {
  const end = todayStr();
  const yyLookup = readJson<{ map?: Record<string, string> }>("yy_lookup.json", { map: {} }).map ?? {};
  // Python 管道积累的 DM 评级 YY 缓存（值可为 null=「DM 无 YY」哨兵），免重复查询
  const yySeed = readJson<Record<string, string | null>>("cache/yy_issuer.json", {});

  // ---- 第一遍：按 30 天窗口拉 YTD 一级发行，做与 Python 版一致的硬性筛选 ----
  type PendingRow = DmRow & { _t: number; _c: number; _plan: number; _actu: number; _amt: number };
  const pending = new Map<string, PendingRow>();
  let winStart = CURVE_START;
  while (winStart <= end) {
    const winEnd = [addDays(winStart, 29), end].sort()[0];
    let rows: DmRow[] = [];
    try {
      rows = (await fetchPrimaryAll(winStart, winEnd, 1)) as unknown as DmRow[];
    } catch {
      /* 单窗口失败跳过 */
    }
    for (const row of rows) {
      const sd = String(row.subscribe_date ?? "").trim();
      if (sd < CURVE_START || sd > end) continue;
      const y = Number(row.issue_yield);
      if (!Number.isFinite(y) || y <= 0) continue; // 无票面（未发行/取消）
      const sid = String(row.security_id ?? "").trim();
      if (!sid || pending.has(sid)) continue;
      const typ = String(row.bond_type_desc ?? "").trim();
      const nm = String(row.sec_short_name ?? "").trim();
      if (typ.includes("PPN") || nm.includes("PPN") || typ.includes("定向")) continue;
      if (
        typ.includes("可转债") || typ.includes("可转换") || typ.includes("可交换") ||
        nm.includes("转债") || nm.includes("可交换") || nm.includes("交换债") || nm.includes("EB")
      ) continue;
      const pub = String(row.public_offering_status ?? "").trim();
      if (/^(283|520)/.test(sid) || pub.includes("私募")) continue;
      const structStr = `${row.bond_matu_struct ?? ""} ${row.bond_issue_tenor ?? ""}`;
      if (/\+\s*N/i.test(structStr) || structStr.includes("永续")) continue;
      const t = preTenor(String(row.bond_matu_struct ?? ""), String(row.bond_issue_tenor ?? ""));
      if (t === null || !(t > 0 && t <= 30.5)) continue;
      const plan = Number(row.plan_issue_amount) || 0;
      const actu = Number(row.actu_issue_amount) || 0;
      pending.set(sid, { ...row, _t: t, _c: y, _plan: plan / 10000, _actu: actu / 10000, _amt: (actu || plan) / 10000 });
    }
    if (winEnd >= end) break;
    winStart = addDays(winEnd, 1);
    await sleep(200);
  }

  // ---- YY 兜底链：yy_lookup（每日 Excel）→ DM 评级缓存种子 → getCompanyRatings/本地映射 ----
  const missIssuers = new Set<string>();
  for (const r of pending.values()) {
    const iss = String(r.issuer_full_name ?? "").trim();
    if (iss && !yyLookup[iss] && !(iss in yySeed) && !localYyOf(iss)) missIssuers.add(iss);
  }
  const ratingMap = missIssuers.size ? await getCompanyRatings([...missIssuers]) : {};
  const yyOf = (iss: string): string | null => {
    if (!iss) return null;
    if (yyLookup[iss]) return yyLookup[iss];
    if (iss in yySeed) return yySeed[iss] || null;
    return ratingMap[iss]?.yy ?? localYyOf(iss) ?? null;
  };

  // ---- 第二遍：YY 1-5 档分档聚合 ----
  const agg = new Map<string, { count: number; amt: number; cw: number; tW: number; cMin: number | null; cMax: number | null }>();
  for (const [name] of CURVE_BUCKETS) agg.set(name, { count: 0, amt: 0, cw: 0, tW: 0, cMin: null, cMax: null });
  const rowsOut: CurveRow[] = [];
  let skippedGt = 0;
  let skippedNoYY = 0;
  for (const r of pending.values()) {
    const iss = String(r.issuer_full_name ?? "").trim();
    const yy = yyOf(iss);
    if (!yy) {
      skippedNoYY++;
      continue;
    }
    if (!yyInScope(yy)) {
      skippedGt++;
      continue;
    }
    const b = bucketOf(r._t);
    if (!b) continue;
    const a = agg.get(b)!;
    a.count++;
    a.amt += r._amt;
    a.cw += r._c * r._amt;
    a.tW += r._t * r._amt;
    a.cMin = a.cMin === null ? r._c : Math.min(a.cMin, r._c);
    a.cMax = a.cMax === null ? r._c : Math.max(a.cMax, r._c);
    rowsOut.push({
      name: String(r.sec_short_name ?? "").trim(),
      date: String(r.subscribe_date ?? "").trim(),
      issuer: iss,
      yy,
      termY: round3(r._t),
      coupon: round4(r._c),
      amtYi: round2(r._amt),
      planAmtYi: round2(r._plan),
      actuAmtYi: round2(r._actu),
      bucket: b,
    });
  }

  const buckets = CURVE_BUCKETS.map(([term]) => {
    const a = agg.get(term)!;
    if (!a.count) {
      return { term, termY: null, count: 0, amountYi: 0, coupon: null, couponMin: null, couponMax: null, bonds: [] as Array<{ name: string; termY: number; coupon: number; amtYi: number }> };
    }
    return {
      term,
      termY: round2(a.tW / a.amt),
      count: a.count,
      amountYi: round2(a.amt),
      coupon: round4(a.cw / a.amt),
      couponMin: round4(a.cMin as number),
      couponMax: round4(a.cMax as number),
      bonds: rowsOut
        .filter((r) => r.bucket === term)
        .sort((x, y) => y.amtYi - x.amtYi)
        .map((r) => ({ name: r.name, termY: r.termY, coupon: r.coupon, amtYi: r.amtYi })),
    };
  });

  const totAmt = [...agg.values()].reduce((s, a) => s + a.amt, 0);
  const totC = [...agg.values()].reduce((s, a) => s + a.cw, 0);
  const totN = [...agg.values()].reduce((s, a) => s + a.count, 0);
  let rangeEnd = CURVE_START;
  for (const r of pending.values()) {
    const d = String(r.subscribe_date ?? "").trim();
    if (d > rangeEnd) rangeEnd = d;
  }
  writeJsonAtomic(path.join(DATA_DIR, "coupon_curve.json"), {
    meta: {
      source: "DM 一级发行主数据(bond/primary/data) · 信用债",
      generated: localStamp(),
      range: `${CURVE_START}~${rangeEnd}`,
      yyScope: "YY 评分 1-5 档（含 1-/1/1+/2-/2/2+/3-/3/3+/4-/4/4+/5-/5/5+）",
      yySource: "每日一级发行 Excel YY 优先，DM 公司评级 YY 兜底",
      clean: "信用债口径：剔 PPN/定向、私募、可转债/可交换、永续(+N)、无票面(未发行/取消)、行权前期限>30.5年；含权债以行权前期限分档",
      bucket: "≤1Y、2Y、3Y、5Y、7Y、10Y、15Y、20Y、30Y（档间取关键期限中点切分）",
      weight: "票面按发行金额加权；金额=DM 实际发行额（actu_issue_amount，与上市公告市场真实发行一致）；计划发行额见个券 planAmtYi（缩量/超募券与计划不同）",
      bondCount: totN,
      amountYi: round2(totAmt),
      couponAvg: totAmt ? round4(totC / totAmt) : null,
      skippedGt,
      skippedNoYY,
    },
    buckets,
    bonds: rowsOut,
  });

  // ---- 逐日快照（日期叠加对比）：每个簿记日 = 当日发行口径（非累计） ----
  const byDay = new Map<string, CurveRow[]>();
  for (const r of rowsOut) {
    if (!byDay.has(r.date)) byDay.set(r.date, []);
    byDay.get(r.date)!.push(r);
  }
  const dates = [...byDay.keys()].sort();
  const series: Record<string, unknown> = {};
  for (const d of dates) {
    const dayRows = byDay.get(d)!;
    const dayAgg = new Map<string, { count: number; amt: number; cw: number; tW: number; bonds: Array<{ name: string; termY: number; coupon: number; amtYi: number }> }>();
    for (const [name] of CURVE_BUCKETS) dayAgg.set(name, { count: 0, amt: 0, cw: 0, tW: 0, bonds: [] });
    for (const r of dayRows) {
      const a = dayAgg.get(r.bucket)!;
      a.count++;
      a.amt += r.amtYi;
      a.cw += r.coupon * r.amtYi;
      a.tW += r.termY * r.amtYi;
      a.bonds.push({ name: r.name, termY: r.termY, coupon: r.coupon, amtYi: r.amtYi });
    }
    const snapBuckets = CURVE_BUCKETS.map(([term]) => {
      const a = dayAgg.get(term)!;
      if (!a.count) return { term, termY: null, count: 0, amountYi: 0, coupon: null, bonds: [] };
      a.bonds.sort((x, y) => y.amtYi - x.amtYi);
      return {
        term,
        termY: round2(a.tW / a.amt),
        count: a.count,
        amountYi: round2(a.amt),
        coupon: round4(a.cw / a.amt),
        bonds: a.bonds,
      };
    });
    const dN = dayAgg && snapBuckets.reduce((s, b) => s + b.count, 0);
    const dAmt = snapBuckets.reduce((s, b) => s + b.amountYi, 0);
    const dC = snapBuckets.reduce((s, b) => s + (b.coupon ?? 0) * b.amountYi, 0);
    series[d] = {
      count: dN,
      amountYi: round2(dAmt),
      couponAvg: dAmt ? round4(dC / dAmt) : null,
      buckets: snapBuckets,
    };
  }
  writeJsonAtomic(path.join(DATA_DIR, "coupon_curve_by_date.json"), {
    dates,
    series,
    meta: {
      start: CURVE_START,
      end: dates.length ? dates[dates.length - 1] : end,
      points: dates.length,
      mode: "当日发行口径：每个日期=该簿记日当日新发的 YY1-5 档个券（非累计）",
      generated: localStamp(),
    },
  });

  return {
    bondCount: totN,
    amountYi: round2(totAmt),
    couponAvg: totAmt ? round4(totC / totAmt) : null,
    range: `${CURVE_START}~${rangeEnd}`,
    skippedGt,
    skippedNoYY,
  };
}

// ==================== 7. 发行人分析 YTD（DM → issuer_ytd.json + issuer_bonds.json） ====================
// scripts/build_issuer_ytd.py 的 TS 移植。issuer_ytd.json 原是打包者机器管道的打包快照，
// 重建不及时（快照停在打包日，其后新发的主体在「发行人分析」页搜不到），与票面曲线同样
// 改为服务器每日从 DM 全量重建（幂等覆盖写）。口径与 Python 版一致：
// 信用债清洗（credit.ts cleanReason：剔 PPN/定向、永续、私募 283/520、可转债/可交换）+ 剔取消发行；
// 计划/实际规模按截标日归属（亿元）；品种/市场规模取「实际优先、缺则计划」；
// 内嵌评级取当晚第 4 步刚刷新的 issuer_ratings_live（读时 /api/issuer/ytd 仍以 live 优先覆盖）。

const ISSUER_YTD_START = "2026-01-01";

interface YtdBondOut {
  name: string;
  code: string;
  tenor: string | null;
  type: string;
  planYi: number;
  actYi: number;
  coupon: number | null;
  date: string;
  market: string;
}

/** DM 日期兼容：字符串 "2026-09-16" 或毫秒时间戳 → "2026-09-16"（同 build_issuer_ytd.py to_date，本地时区） */
function dmDate10(v: unknown): string {
  const s = String(v ?? "").trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  const n = Number(s);
  if (s && Number.isFinite(n) && n > 1e12) {
    const d = new Date(n);
    const p = (x: number) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  return "";
}

/** 与 build_issuer_ytd.py market_of 一致：.IB 或 10/11/12 开头=银行间，.SH/.SZ 后缀=交易所 */
function marketOfCode(code: unknown): string {
  const c = String(code ?? "");
  if (/\.ib$/i.test(c) || c.toUpperCase().includes(".IB") || /^(10|11|12)/.test(c)) return "银行间";
  if (/\.(sh|sz)$/i.test(c)) return "交易所";
  if (c.includes("交易所")) return "交易所";
  return "其他";
}

/** 与 build_issuer_ytd.py tenor_key 一致：期限字符串→年数（D/365、W/52、M/12），无法解析 -1 */
function tenorYears(s: string | null | undefined): number {
  const m = /([\d.]+)\s*([YMDW])?/i.exec(String(s ?? ""));
  if (!m) return -1;
  const v = parseFloat(m[1]);
  if (!Number.isFinite(v)) return -1;
  const u = (m[2] || "Y").toUpperCase();
  if (u === "D") return v / 365;
  if (u === "W") return v / 52;
  if (u === "M") return v / 12;
  return v;
}

/** 计数器取众数（并列取先出现者，与 Python Counter.most_common(1) 一致） */
function topOfCounter(m: Map<string, number>): string | null {
  let best: string | null = null;
  let bestN = -1;
  for (const [k, n] of m) {
    if (n > bestN) {
      best = k;
      bestN = n;
    }
  }
  return best;
}

export async function refreshIssuerYtd() {
  const t0 = Date.now();
  const start = ISSUER_YTD_START;
  const end = todayStr();

  // ---- 第一遍：30 天窗口拉 YTD 一级发行全量（按 security_id 去重，同 refreshCouponCurve）----
  const seen = new Set<string>();
  const rows: Array<DmRow & { _sd: string }> = [];
  let winStart = start;
  while (winStart <= end) {
    const winEnd = [addDays(winStart, 29), end].sort()[0];
    let batch: DmRow[] = [];
    try {
      batch = (await fetchPrimaryAll(winStart, winEnd, 1)) as unknown as DmRow[];
    } catch {
      /* 单窗口失败跳过 */
    }
    for (const b of batch) {
      const sid = String(b.security_id ?? "").trim();
      const sd = dmDate10(b.subscribe_date);
      if (!sid || !sd || sd < start || sd > end || seen.has(sid)) continue;
      seen.add(sid);
      rows.push({ ...b, _sd: sd });
    }
    if (winEnd >= end) break;
    winStart = addDays(winEnd, 1);
    await sleep(200);
  }

  // ---- 第二遍：信用债清洗（剔 PPN/定向、永续、私募、转债/可交换、取消发行）后按主体聚合 ----
  const dropped = { clean: 0, cancel: 0 };
  const monthly = new Map<string, { cnt: number; plan: number; act: number }>();
  const typeAgg = new Map<string, { cnt: number; amt: number }>();
  const marketAgg = new Map<string, { cnt: number; amt: number }>();
  const byIssuer = new Map<
    string,
    {
      cnt: number;
      plan: number;
      act: number;
      couponSum: number;
      couponN: number;
      types: Map<string, number>;
      markets: Map<string, number>;
      province: string;
      first: string;
      last: string;
      bonds: YtdBondOut[];
    }
  >();

  for (const b of rows) {
    if (cleanReason(b as Parameters<typeof cleanReason>[0])) {
      dropped.clean++;
      continue;
    }
    if (String(b.issue_status_desc ?? "").includes("取消")) {
      dropped.cancel++;
      continue;
    }
    const sd = b._sd;
    const planYi = (Number(b.plan_issue_amount) || 0) / 10000;
    const actYi = (Number(b.actu_issue_amount) || 0) / 10000;
    const amtYi = actYi > 0 ? actYi : planYi; // 品种/市场规模口径：实际优先、缺则计划
    const y = Number(b.issue_yield);
    const coupon = Number.isFinite(y) && y > 0 ? y : null;
    const type = String(b.bond_type_desc ?? "").trim() || "其他";
    const market = marketOfCode(b.security_id);
    const ym = sd.slice(0, 7);

    const m = monthly.get(ym) ?? { cnt: 0, plan: 0, act: 0 };
    m.cnt++;
    m.plan += planYi;
    m.act += actYi;
    monthly.set(ym, m);
    const ta = typeAgg.get(type) ?? { cnt: 0, amt: 0 };
    ta.cnt++;
    ta.amt += amtYi;
    typeAgg.set(type, ta);
    const ma = marketAgg.get(market) ?? { cnt: 0, amt: 0 };
    ma.cnt++;
    ma.amt += amtYi;
    marketAgg.set(market, ma);

    const name = String(b.issuer_full_name ?? "").trim() || "未知主体";
    let g = byIssuer.get(name);
    if (!g) {
      g = {
        cnt: 0, plan: 0, act: 0, couponSum: 0, couponN: 0,
        types: new Map(), markets: new Map(), province: "",
        first: sd, last: sd, bonds: [],
      };
      byIssuer.set(name, g);
    }
    g.cnt++;
    g.plan += planYi;
    g.act += actYi;
    if (coupon !== null) {
      g.couponSum += coupon;
      g.couponN++;
    }
    g.types.set(type, (g.types.get(type) ?? 0) + 1);
    g.markets.set(market, (g.markets.get(market) ?? 0) + 1);
    if (!g.province) g.province = String(b.province_name ?? "").trim();
    if (sd < g.first) g.first = sd;
    if (sd > g.last) g.last = sd;
    g.bonds.push({
      name: String(b.sec_short_name ?? "").trim(),
      code: String(b.security_id ?? "").trim(),
      tenor: String(b.bond_issue_tenor ?? "").trim() || null,
      type,
      planYi: round2(planYi),
      actYi: round2(actYi),
      coupon: coupon !== null ? Math.round(coupon * 1000) / 1000 : null,
      date: sd,
      market,
    });
  }

  // ---- 评级内嵌（第 4 步 live 刚刷新；YY 再经 localYyOf 兜底每日 Excel yy_lookup；
  //      读时 API 仍以 live 优先覆盖，双保险）----
  const live =
    readJson<{ map?: Record<string, { yy?: string | null; external?: string | null }> }>(
      "cache/issuer_ratings_live.json",
      { map: {} }
    ).map ?? {};

  const issuersOut = [...byIssuer.entries()].map(([name, g]) => ({
    issuer: name,
    cnt: g.cnt,
    planYi: round2(g.plan),
    actYi: round2(g.act),
    couponAvg: g.couponN ? round2(g.couponSum / g.couponN) : null,
    topType: topOfCounter(g.types),
    topMarket: topOfCounter(g.markets),
    exCount: g.markets.get("交易所") ?? 0,
    province: g.province,
    ratingExt: live[name]?.external ?? null,
    yy: live[name]?.yy ?? localYyOf(name) ?? null,
    firstDate: g.first,
    lastDate: g.last,
  }));
  issuersOut.sort((a, b) => b.planYi - a.planYi || b.cnt - a.cnt);

  // ---- 个券明细：期限从长到短（同 Python tenor_key），主体按名字典序 ----
  const bondsOut: Record<string, YtdBondOut[]> = {};
  for (const [name, g] of byIssuer) {
    g.bonds.sort(
      (x, y) =>
        tenorYears(y.tenor) - tenorYears(x.tenor) ||
        (x.date < y.date ? -1 : x.date > y.date ? 1 : 0) ||
        (x.name < y.name ? -1 : 1)
    );
    bondsOut[name] = g.bonds;
  }

  const monthlyArr = [...monthly.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([ym, v]) => ({ ym, cnt: v.cnt, planYi: round2(v.plan), actYi: round2(v.act) }));
  const typesArr = [...typeAgg.entries()]
    .sort((a, b) => b[1].cnt - a[1].cnt)
    .slice(0, 12)
    .map(([t, v]) => ({ type: t, cnt: v.cnt, planYi: round2(v.amt) }));
  const marketsArr = [...marketAgg.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([m, v]) => ({ market: m, cnt: v.cnt, planYi: round2(v.amt) }));

  const range = `${start}~${end}`;
  const bondCount = [...byIssuer.values()].reduce((s, g) => s + g.cnt, 0);
  const planTotal = issuersOut.reduce((s, x) => s + x.planYi, 0);
  const actTotal = issuersOut.reduce((s, x) => s + x.actYi, 0);

  writeJsonAtomic(path.join(DATA_DIR, "issuer_bonds.json"), {
    meta: { generated: localStamp(), range, issuerCount: byIssuer.size, bondCount },
    issuers: Object.fromEntries(Object.keys(bondsOut).sort().map((k) => [k, bondsOut[k]])),
  });
  writeJsonAtomic(path.join(DATA_DIR, "issuer_ytd.json"), {
    meta: {
      source: "DM 一级发行(primary/data) 信用债（服务器每日自动重建）",
      generated: localStamp(),
      range,
      clean: "信用债口径：剔 PPN/定向、永续、私募(283/520)、可转债/可交换、取消发行",
      issuerCount: byIssuer.size,
      bondCount,
      planYi: round2(planTotal),
      actYi: round2(actTotal),
    },
    monthly: monthlyArr,
    types: typesArr,
    markets: marketsArr,
    issuers: issuersOut,
  });

  return {
    issuers: byIssuer.size,
    bonds: bondCount,
    droppedClean: dropped.clean,
    droppedCancel: dropped.cancel,
    range,
    ms: Date.now() - t0,
  };
}

// ==================== 编排 + 状态 + 调度 ====================

export interface RefreshState {
  lastRun?: string;
  lastOkDate?: string;
  summary?: Record<string, unknown>;
  error?: string;
}

export function readRefreshState(): RefreshState {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) as RefreshState;
  } catch {
    return {};
  }
}

function writeRefreshState(s: RefreshState) {
  try {
    writeJsonAtomic(STATE_FILE, s);
  } catch {
    /* 状态写失败不影响刷新本身 */
  }
}

let running = false;

/**
 * 执行一轮完整刷新（日历补数 → 记录回填 → 估值 → 30Y 国债 → 发行人评级 → 票面曲线 → 发行人 YTD）。
 * 单飞：并发调用返回 skipped。steps 传步骤名子集（如 ["issuerYtd"]）时仅跑指定步骤（补跑用，
 * 不更新 lastOkDate，不影响当晚整点自动全量刷新）。
 */
export async function runDailyRefresh(
  ratingsLimit = 0,
  steps?: string[]
): Promise<{ skipped?: boolean; summary?: Record<string, unknown>; error?: string }> {
  if (running) return { skipped: true, summary: readRefreshState().summary };
  if (!process.env.INNO_APP_KEY || !process.env.INNO_APP_SECRET) {
    return { error: "未配置 DM 凭证（INNO_APP_KEY / INNO_APP_SECRET），无法自动刷新" };
  }
  const want = (s: string) => !steps || steps.includes(s);
  const partial = !!steps && steps.length > 0;
  running = true;
  const state: RefreshState = { lastRun: new Date().toISOString() };
  // 单步补跑不清 lastOkDate：保留上一次全量刷新的记录，避免调度器当晚重复全量跑
  if (partial) {
    const prev = readRefreshState();
    if (prev.lastOkDate) state.lastOkDate = prev.lastOkDate;
  }
  // 单步失败不拖垮整轮（先完成的步骤已落盘），错误记入 summary
  const safe = async (fn: () => Promise<Record<string, unknown>>): Promise<Record<string, unknown>> => {
    try {
      return await fn();
    } catch (e) {
      return { error: e instanceof Error ? e.message : String(e) };
    }
  };
  const skip = () => ({ skipped: true }) as Record<string, unknown>;
  try {
    const calendar = want("calendar") ? await refreshCalendar(10) : skip();
    // 回填在估值前：新补的 securityId 当晚即可被估值覆盖
    const bidsInfo = want("bids") ? await safe(() => refreshBidsInfo()) : skip();
    const valuations = want("valuations") ? await refreshValuations() : skip();
    const gov30y = want("gov30y") ? await refreshGov30y() : skip();
    const issuerRatings = want("issuerRatings") ? await safe(() => refreshIssuerRatings(ratingsLimit)) : skip();
    const couponCurve = want("couponCurve") ? await safe(() => refreshCouponCurve()) : skip();
    // 发行人 YTD 在评级之后：内嵌评级取当晚刚刷新的 live 值
    const issuerYtd = want("issuerYtd") ? await safe(() => refreshIssuerYtd()) : skip();
    if (!partial) state.lastOkDate = todayStr();
    state.summary = { calendar, bidsInfo, valuations, gov30y, issuerRatings, couponCurve, issuerYtd, finishedAt: new Date().toISOString() };
    writeRefreshState(state);
    return { summary: state.summary };
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e);
    writeRefreshState(state);
    return { error: state.error };
  } finally {
    running = false;
  }
}

/**
 * 每日自动调度：到达 DM_REFRESH_AT（默认 19:00，当日发行结果基本落定）且今日尚未成功刷新时触发。
 * 由 src/instrumentation.ts 在服务器启动时调用；服务中途重启会在下一个检查点补跑当日任务。
 */
export function startRefreshScheduler() {
  const check = async () => {
    try {
      if (!process.env.INNO_APP_KEY || !process.env.INNO_APP_SECRET) return; // 未配凭证静默跳过
      const state = readRefreshState();
      if (state.lastOkDate === todayStr()) return;
      const [h, m] = String(process.env.DM_REFRESH_AT || "19:00").split(":").map(Number);
      const now = new Date();
      const target = new Date(now);
      target.setHours(h || 19, m ?? 0, 0, 0);
      if (now < target) return;
      const r = await runDailyRefresh();
      if (r.error) console.warn("[dm-refresh] 每日自动刷新失败:", r.error);
      else console.log("[dm-refresh] 每日自动刷新完成");
    } catch (e) {
      console.warn("[dm-refresh] 调度检查异常:", e instanceof Error ? e.message : e);
    }
  };
  const t1 = setTimeout(check, 30_000); // 启动 30 秒后先检查一次（补跑重启前错过的当日刷新）
  const t2 = setInterval(check, 10 * 60_000); // 之后每 10 分钟检查一次
  t1.unref?.();
  t2.unref?.();
}
