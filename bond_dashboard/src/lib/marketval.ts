// 可比券(相似二级券)估值行权化
// 每日/利差页展示的 similar_bond_cb_valuation 来自 DM 一级发行快照，
// 本模块按可比券代码回查 DM market-data/date，含权券一律取「行权收益率」(yte)，
// 非含权取到期(ytm)，用结果覆写，带磁盘缓存 data/cache/similar_vals.json。
import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { postData } from "@/lib/dm-client";

const MARKET_DATE = "/dm-quant-func-service/api/v1/bond/market-data/date";
const CACHE_FILE = path.join(DATA_DIR, "cache", "similar_vals.json");

/** 从市场行情行中取值：含权(yte 存在)一律行权，否则 ytm */
function pick(ytm: unknown, yte: unknown): number | null {
  if (yte !== null && yte !== undefined && String(yte).trim() !== "" && !Number.isNaN(Number(yte))) return Number(yte);
  if (ytm !== null && ytm !== undefined && String(ytm).trim() !== "" && !Number.isNaN(Number(ytm))) return Number(ytm);
  return null;
}

/** 该可比券对应估值：银行间 .IB 取中债(cb)，交易所 .SH/.SZ 取中证(cs)，缺失则回退另一侧 */
function rowVal(cb: number | null, cs: number | null, code: string): number | null {
  if (code.endsWith(".IB")) return cb ?? cs;
  return cs ?? cb;
}

interface CacheEntry {
  date: string;
  cb: number | null;
  cs: number | null;
}
function readCache(): Record<string, CacheEntry> {
  try {
    if (!fs.existsSync(CACHE_FILE)) return {};
    return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) as Record<string, CacheEntry>;
  } catch {
    return {};
  }
}
function writeCache(cache: Record<string, CacheEntry>) {
  try {
    fs.mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
    const keys = Object.keys(cache);
    if (keys.length > 4000) {
      for (const k of keys.slice(0, keys.length - 4000)) delete cache[k];
    }
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache), "utf8");
  } catch {
    /* 缓存失败不阻塞 */
  }
}

function addDays(date: string, n: number): string {
  const d = new Date(date + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

interface SimilarRow {
  similar_bond_code?: string | null;
  similar_bond_cb_valuation?: number | null;
  subscribe_date?: string | null;
}

/**
 * 将可比券估值替换为行权口径：
 * 目标估值日 = subscribe_date 的前一自然日，逐日向前回溯（最多 10 天），取首个有行情的日期。
 */
export async function enrichSimilarValuation<T extends SimilarRow>(list: T[]): Promise<T[]> {
  const cache = readCache();
  const byKey = new Map<string, T[]>(); // `${code}|${target}` -> 待覆写行
  for (const b of list) {
    const code = String(b.similar_bond_code ?? "").trim();
    const val = b.similar_bond_cb_valuation;
    if (!code || val === null || val === undefined || Number.isNaN(val)) continue;
    const sub = String(b.subscribe_date ?? "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(sub)) continue;
    const target = addDays(sub, -1);
    const key = `${code}|${target}`;
    if (cache[key]) {
      // 已有缓存：直接覆写
      const e = cache[key];
      const v = rowVal(e.cb, e.cs, code);
      if (v !== null) (b as Record<string, unknown>).similar_bond_cb_valuation = v;
      continue;
    }
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key)!.push(b);
  }
  if (!byKey.size) return list;

  const resolved = new Set<string>();
  for (let off = 1; off <= 10 && byKey.size > resolved.size; off++) {
    // cand -> [{key, code, target}]
    const want = new Map<string, { key: string; code: string }[]>();
    for (const key of byKey.keys()) {
      if (resolved.has(key)) continue;
      const code = key.split("|")[0];
      const target = key.slice(code.length + 1);
      const cand = addDays(target, off - 1);
      if (!want.has(cand)) want.set(cand, []);
      want.get(cand)!.push({ key, code });
    }
    for (const [cand, items] of want) {
      const codes = [...new Set(items.map((i) => i.code))];
      for (let i = 0; i < codes.length; i += 5) {
        const chunk = codes.slice(i, i + 5);
        let rows: Array<Record<string, unknown>> = [];
        try {
          const resp = await postData<Record<string, unknown>[] | { list?: unknown[] }>(
            { securityIdList: chunk, dataSourceList: [2], startDate: cand, endDate: cand },
            MARKET_DATE
          );
          rows = (Array.isArray(resp) ? resp : (resp?.list as Record<string, unknown>[]) ?? []) as Record<string, unknown>[];
        } catch {
          rows = [];
        }
        const got = new Map<string, { cb: number | null; cs: number | null }>();
        for (const r of rows) {
          const sid = String(r.security_id ?? "");
          if (!sid) continue;
          got.set(sid, { cb: pick(r.cb_ytm, r.cb_yte), cs: pick(r.cs_ytm, r.cs_yte) });
        }
        for (const { key, code } of items) {
          if (resolved.has(key)) continue;
          const g = got.get(code);
          if (!g) continue;
          cache[key] = { date: cand, cb: g.cb, cs: g.cs };
          resolved.add(key);
          const v = rowVal(g.cb, g.cs, code);
          if (v !== null) {
            for (const b of byKey.get(key) ?? []) (b as Record<string, unknown>).similar_bond_cb_valuation = v;
          }
        }
      }
    }
  }
  writeCache(cache);
  return list;
}
