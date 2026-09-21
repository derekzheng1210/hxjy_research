// Excel 上传导入（网页上传模式，替代原依赖打包者本机路径的脚本）
// 两种文件：
//  1) 每日发行《一级发行-信用债发行 YYYY-MM-DD.xlsx》→ excel_calendar.json（当日整日覆盖）
//     + recommended.json（仅替换同日推荐条目，其他日期不动）+ yy_lookup.json（增量累积）
//  2) 《一级中标登记表.xlsx》→ 投标记录（按(发行日,简称)分组：参与=Σ投资量、中标=Σ中标量>0；
//     已存在 (bidDate,bondName,type) 的记录不重复新建，仅回填缺失字段）
// 合并语义沿用原 Python 脚本 update_recent.py / sync_bids.py / sync_excel_calendar.py。
import { DATA_DIR } from "@/lib/data-dir";
import * as XLSX from "xlsx";
import fs from "node:fs";
import path from "node:path";
import { addBid, updateBid, getBids } from "./store";
import type { ExcelBond } from "./types";


// ==================== 通用工具 ====================

function readJson<T>(rel: string, fallback: T): T {
  try {
    const p = path.join(DATA_DIR, rel);
    if (!fs.existsSync(p)) return fallback;
    return JSON.parse(fs.readFileSync(p, "utf8")) as T;
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(rel: string, data: unknown) {
  const p = path.join(DATA_DIR, rel);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = p + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data, null, 1), "utf8");
  fs.renameSync(tmp, p);
}

function nowStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 数值清洗（与 update_recent.py 的 to_num 一致）：空白/--/-/N/A 视为空；去逗号百分号；0 视为空 */
function toNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/\u00a0/g, " ").replace(/\u3000/g, " ").trim().replace(/^['"]|['"]$/g, "");
  if (["", "--", "-", "None", "N/A", "#N/A"].includes(s)) return null;
  const f = Number(s.replace(/,/g, "").replace(/%/g, ""));
  if (Number.isNaN(f)) return null;
  return f !== 0 ? f : null;
}

function text(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return s && s.toLowerCase() !== "none" ? s : null;
}

function dateOf(v: unknown): string | null {
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(String(v ?? ""));
  return m ? m[0] : null;
}

/** 全站口径：剔除 PPN/定向工具（与 /api/recommended、/api/dm/primary 的过滤一致） */
function isPPNRow(bondType: string | null, name: string): boolean {
  const t = String(bondType ?? "");
  return t.includes("PPN") || t.includes("定向") || name.includes("PPN");
}

// ==================== 表头定位（前 4 行内查找，兼容多行表头） ====================

type ColMap = Record<string, number>;

function findHeader(rows: unknown[][], matchers: [string, (s: string) => boolean][]): { headerRow: number; cols: ColMap } | null {
  for (let r = 0; r < Math.min(4, rows.length); r++) {
    const cols: ColMap = {};
    let hits = 0;
    for (let c = 0; c < rows[r].length; c++) {
      const v = String(rows[r][c] ?? "").trim();
      if (!v) continue;
      for (const [key, test] of matchers) {
        if (cols[key] === undefined && test(v)) {
          cols[key] = c;
          hits++;
          break;
        }
      }
    }
    if (hits >= 2) return { headerRow: r, cols }; // 至少命中2列才认定是表头行
  }
  return null;
}

// ==================== 1) 每日发行 Excel ====================

interface DailyRow {
  name: string;
  code: string | null;
  term: string | null;
  plan: number | null;
  coupon: number | null;
  couponRaw: string | null;
  forecast: number | null;
  spBp: number | null;
  issuer: string | null;
  region: string | null;
  bondType: string | null;
  recommended: boolean;
  yy: string | null;
  payDate: string | null;
  rating: string | null;
  secBond: string | null;
  secVal: number | null;
}

export interface DailyImportResult {
  date: string;
  total: number;
  planYi: number;
  recommendedCount: number;
  newRecommended: number;
  yyAdded: number;
  dayExisted: boolean;
}

export async function importDailyExcel(buf: Buffer, fileName: string): Promise<DailyImportResult> {
  const wb = XLSX.read(buf, { type: "buffer" });
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json<unknown[]>(ws, { header: 1, defval: null, raw: false });
  if (!rows.length) throw new Error("Excel 内容为空");

  const head = findHeader(rows, [
    ["name", (s) => /^债券简称/.test(s)],
    ["code", (s) => /^债券代码/.test(s)],
    ["term", (s) => /^发行期限/.test(s)],
    ["plan", (s) => /^计划发行/.test(s)],
    // 票面利率列：以「票面」开头但排除「票面-预测」
    ["coupon", (s) => /^票面/.test(s) && !s.startsWith("票面-预测")],
    ["forecast", (s) => /^新债预测/.test(s)],
    ["sp", (s) => /^票面-预测/.test(s)],
    ["issuer", (s) => /^发行人/.test(s)],
    ["region", (s) => /^区域/.test(s)],
    ["type", (s) => /^债券类型/.test(s)],
    ["rec", (s) => /^是否推荐/.test(s)],
    ["yy", (s) => /^YY评分/.test(s)],
    ["pay", (s) => /^缴款日/.test(s)],
    ["rating", (s) => /^主体评级/.test(s)],
    ["secBond", (s) => /^相似二级券/.test(s)],
    ["secVal", (s) => /^相似二级估值/.test(s)],
  ]);
  if (!head || head.cols.name === undefined) {
    throw new Error("未找到表头（需包含「债券简称」列，请确认是每日发行清单文件）");
  }
  const c = head.cols;

  // 日期：优先文件名（微信重传会带 (1) 等后缀，正则容错），其次工作表名
  const date = dateOf(fileName) ?? dateOf(wb.SheetNames[0] ?? "");
  if (!date) {
    throw new Error("无法解析发行日期：文件名/工作表名中需包含 YYYY-MM-DD");
  }

  const parsed: DailyRow[] = [];
  for (let r = head.headerRow + 1; r < rows.length; r++) {
    const row = rows[r] ?? [];
    const name = text(row[c.name]);
    if (!name) continue;
    const couponCell = row[c.coupon] ?? null;
    // 票面列文字标注（回拨/取消发行）保留为 couponRaw，不写入数值票面
    const couponRaw =
      couponCell !== null && typeof couponCell === "string" && /回拨|取消/.test(couponCell.trim())
        ? couponCell.trim()
        : null;
    const coupon = toNum(couponCell);
    const forecast = c.forecast !== undefined ? toNum(row[c.forecast]) : null;
    const spExcel = c.sp !== undefined ? toNum(row[c.sp]) : null;
    const plan = c.plan !== undefined ? toNum(row[c.plan]) : null;
    parsed.push({
      name,
      code: c.code !== undefined ? text(row[c.code]) : null,
      term: c.term !== undefined ? text(row[c.term]) : null,
      plan,
      coupon,
      couponRaw,
      forecast,
      // 票面-预测列优先（已是 bp），缺失时用（票面-预测）×100 兜底
      spBp: spExcel !== null ? spExcel : coupon !== null && forecast !== null ? Math.round((coupon - forecast) * 1000) / 10 : null,
      issuer: c.issuer !== undefined ? text(row[c.issuer]) : null,
      region: c.region !== undefined ? text(row[c.region]) : null,
      bondType: c.type !== undefined ? text(row[c.type]) : null,
      recommended: c.rec !== undefined ? String(row[c.rec] ?? "").trim() === "是" : false,
      yy: c.yy !== undefined ? text(row[c.yy]) : null,
      payDate: c.pay !== undefined ? (dateOf(row[c.pay]) ?? text(row[c.pay])) : null,
      rating: c.rating !== undefined ? text(row[c.rating]) : null,
      secBond: c.secBond !== undefined ? text(row[c.secBond]) : null,
      secVal: c.secVal !== undefined ? toNum(row[c.secVal]) : null,
    });
  }
  if (!parsed.length) throw new Error("未解析到数据行（表头下无有效「债券简称」）");

  // ---- 1a. excel_calendar.json：当日整日覆盖（重复上传同一天幂等） ----
  interface CalFile {
    meta: { source?: string; generated?: string; fileCount?: number };
    days: Record<string, { file: string; count: number; planYi: number; bonds: ExcelBond[] }>;
  }
  const cal = readJson<CalFile>("excel_calendar.json", { meta: {}, days: {} });
  const dayExisted = !!cal.days[date];
  const bonds: ExcelBond[] = parsed.map((r) => ({
    name: r.name,
    amountYi: r.plan ?? 0,
    type: r.bondType,
    tenor: r.term,
    issuer: r.issuer,
    yy: r.yy,
    forecast: r.forecast,
    coupon: r.coupon,
    payDate: r.payDate,
    recommended: r.recommended,
  }));
  cal.days[date] = {
    file: fileName,
    count: parsed.length,
    planYi: Math.round(parsed.reduce((s, r) => s + (r.plan ?? 0), 0) * 100) / 100,
    bonds,
  };
  cal.meta.generated = nowStr();
  cal.meta.fileCount = Object.keys(cal.days).length;
  writeJsonAtomic("excel_calendar.json", cal);

  // ---- 1b. yy_lookup.json：主体→YY 增量累积（Excel 为权威，覆盖同主体旧值） ----
  const yyFile = readJson<{ meta: { generated?: string; count?: number }; map: Record<string, string> }>(
    "yy_lookup.json",
    { meta: {}, map: {} }
  );
  let yyAdded = 0;
  for (const r of parsed) {
    if (r.issuer && r.yy) {
      if (yyFile.map[r.issuer] !== r.yy) yyAdded++;
      yyFile.map[r.issuer] = r.yy;
    }
  }
  yyFile.meta = { generated: nowStr(), count: Object.keys(yyFile.map).length };
  writeJsonAtomic("yy_lookup.json", yyFile);

  // ---- 1c. recommended.json：仅替换同日推荐条目，其他日期完全不动（保留手工修正） ----
  // 全站口径：PPN/定向不入推荐
  const recRows = parsed.filter((r) => r.recommended && !isPPNRow(r.bondType, r.name));
  const rec = readJson<Record<string, unknown>[]>("recommended.json", []);
  const others = rec.filter((x) => String(x.date ?? "") !== date);
  const newRec = recRows.map((r) => {
    const item: Record<string, unknown> = {
      date,
      name: r.name,
      code: r.code,
      term: r.term,
      plan: r.plan,
      actual: r.plan, // 初始=计划，后续由 DM 每日刷新回填实际发行规模
      coupon: r.coupon,
      forecast: r.forecast,
      sp_bp: r.spBp,
      sec_bond: r.secBond,
      sec_val: r.secVal,
      issuer: r.issuer,
      rating: r.rating,
      region: r.region,
      bond_type: r.bondType,
      yy: r.yy,
      pay_date: r.payDate,
    };
    if (r.couponRaw) item.coupon_raw = r.couponRaw;
    return item;
  });
  writeJsonAtomic("recommended.json", [...others, ...newRec].sort((a, b) => String(a.date).localeCompare(String(b.date))));

  return {
    date,
    total: parsed.length,
    planYi: cal.days[date].planYi,
    recommendedCount: recRows.length,
    newRecommended: newRec.length,
    yyAdded,
    dayExisted,
  };
}

// ==================== 2) 一级中标登记表 ====================

interface BidGroup {
  inv: number;
  win: number;
  managers: string[];
  brokers: string[];
  winManagers: string[];
  coupon: number | null;
  code: string | null;
  term: string | null;
  yy: string | null;
}

export interface BidsImportResult {
  groups: number;
  wonGroups: number;
  addedParticipated: number;
  addedWon: number;
  backfilled: number;
}

/** 解析 MM-DD / YYYY-MM-DD；无年份时按当前年补齐（登记表惯例同年） */
function parseBidDate(v: unknown): string | null {
  const s = String(v ?? "").trim();
  let m = /(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (m) return m[0];
  m = /(\d{2})-(\d{2})/.exec(s);
  if (m) return `${new Date().getFullYear()}-${m[1]}-${m[2]}`;
  return null;
}

export async function importBidsExcel(buf: Buffer): Promise<BidsImportResult> {
  const wb = XLSX.read(buf, { type: "buffer" });
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json<unknown[]>(ws, { header: 1, defval: null, raw: false });
  if (!rows.length) throw new Error("Excel 内容为空");

  const head = findHeader(rows, [
    ["name", (s) => /^债券简称/.test(s)],
    ["code", (s) => /^债券代码/.test(s)],
    ["time", (s) => /^发行时间/.test(s)],
    ["term", (s) => /^发行期限/.test(s)],
    ["manager", (s) => /^投资经理/.test(s)],
    ["broker", (s) => /^参与券商/.test(s)],
    ["inv", (s) => s === "投资量"],
    ["win", (s) => /^中标量/.test(s)],
    ["coupon", (s) => /^票面/.test(s)],
    ["yy", (s) => /^YY评分/.test(s)],
  ]);
  if (!head || head.cols.name === undefined || head.cols.time === undefined || head.cols.inv === undefined || head.cols.win === undefined) {
    throw new Error("未找到登记表表头（需包含「债券简称 / 发行时间 / 投资量 / 中标量」列）");
  }
  const c = head.cols;

  // 按 (发行日, 债券简称) 分组
  const groups = new Map<string, BidGroup>();
  for (let r = head.headerRow + 1; r < rows.length; r++) {
    const row = rows[r] ?? [];
    const name = text(row[c.name]);
    if (!name) continue;
    const d = parseBidDate(row[c.time]);
    if (!d) continue; // 无法解析日期的行跳过
    const key = `${d}|${name}`;
    const g = groups.get(key) ?? { inv: 0, win: 0, managers: [], brokers: [], winManagers: [], coupon: null, code: null, term: null, yy: null };
    const mgr = text(row[c.manager]) ?? "";
    const brk = text(row[c.broker]) ?? "";
    if (mgr && !g.managers.includes(mgr)) g.managers.push(mgr);
    if (brk && !g.brokers.includes(brk)) g.brokers.push(brk);
    g.inv += toNum(row[c.inv]) ?? 0;
    const wv = row[c.win];
    const ws_ = String(wv ?? "").trim();
    if (ws_ && ws_ !== "未中标" && ws_ !== "None") {
      const amt = Number(ws_.replace(/,/g, ""));
      if (!Number.isNaN(amt)) {
        g.win += amt;
        if (mgr && !g.winManagers.includes(mgr)) g.winManagers.push(mgr);
      }
    }
    if (g.coupon === null) g.coupon = toNum(row[c.coupon]);
    if (!g.code) g.code = text(row[c.code]);
    if (!g.term) g.term = text(row[c.term]);
    if (!g.yy) {
      const yv = text(row[c.yy]);
      // YY 档位形如 1/2/3/4/5 或 4+/2-；小数等异常值不作为 YY（历史单元格错位问题）
      if (yv && /^[1-5][+-]?$/.test(yv)) g.yy = yv;
    }
    groups.set(key, g);
  }
  if (!groups.size) throw new Error("未解析到有效数据行");

  // 幂等合并进 SQLite（store 层自动同步 bids.json 镜像）
  const existing = new Map<string, string>();
  for (const b of getBids()) existing.set(`${b.bidDate}|${b.bondName}|${b.type}`, b.id);

  let addedParticipated = 0;
  let addedWon = 0;
  let backfilled = 0;

  for (const [key, g] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const [d, name] = key.split("|");
    const note =
      (g.managers.length ? `投资经理: ${g.managers.join("、")}` : "") +
      (g.brokers.length ? `${g.managers.length ? "；" : ""}参与券商: ${g.brokers.join("、")}` : "");
    const wonNote = g.winManagers.length ? `投资经理: ${g.winManagers.join("、")}` : "";

    if (!existing.has(`${d}|${name}|participated`)) {
      addBid({
        type: "participated",
        bondName: name,
        securityId: g.code ?? undefined,
        term: g.term ?? undefined,
        amount: Math.round(g.inv * 100) / 100,
        coupon: g.coupon ?? undefined,
        yy: g.yy ?? undefined,
        bidDate: d,
        note: note || undefined,
      });
      existing.set(`${d}|${name}|participated`, "new");
      addedParticipated++;
    }
    if (g.win > 0 && !existing.has(`${d}|${name}|won`)) {
      addBid({
        type: "won",
        bondName: name,
        securityId: g.code ?? undefined,
        term: g.term ?? undefined,
        amount: Math.round(g.win * 100) / 100,
        coupon: g.coupon ?? undefined,
        yy: g.yy ?? undefined,
        bidDate: d,
        note: wonNote || undefined,
      });
      existing.set(`${d}|${name}|won`, "new");
      addedWon++;
    }
  }

  // 回填：已存在记录缺票面/期限/代码/YY 时用登记表补齐（不覆盖已有值）
  const bidsNow = getBids();
  for (const [key, g] of groups) {
    const [d, name] = key.split("|");
    for (const t of ["participated", "won"] as const) {
      const rec = bidsNow.find((b) => b.bidDate === d && b.bondName === name && b.type === t);
      if (!rec) continue;
      const patch: Record<string, unknown> = {};
      if (rec.coupon == null && g.coupon != null) patch.coupon = g.coupon;
      if (!rec.term && g.term) patch.term = g.term;
      if (!rec.securityId && g.code) patch.securityId = g.code;
      if (!rec.yy && g.yy) patch.yy = g.yy;
      if (Object.keys(patch).length) {
        updateBid(rec.id, patch);
        backfilled++;
      }
    }
  }

  return {
    groups: groups.size,
    wonGroups: [...groups.values()].filter((g) => g.win > 0).length,
    addedParticipated,
    addedWon,
    backfilled,
  };
}
