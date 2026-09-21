// 个券上市后估值序列（发行票面 vs 最新估值 + 发行以来分位数）
// 数据源：DM bond/market-data/date（支持日期区间 ≤3 个月，每批最多 5 券），
// 行字段 issue_date = 估值日期；估值口径与 fetch_valuations.py 一致：
// 含权债取行权收益率(yte)，非含权取到期收益率(ytm)；银行间(.IB)取中债，交易所(.SH/.SZ)取中证。
// 磁盘缓存 data/cache/own_series.json：{ code: { u: 最后拉取时刻, s: 拉取起点, vals: [{d, v}] } }，按日增量续拉；
// 新鲜判断以「最后估值日 >= 今天」为准（当日估值 DM 晚间发布，白天拉过不锁死），无数据券 20 点前允许当日重试。
import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { postData } from "@/lib/dm-client";

const MARKET_DATE = "/dm-quant-func-service/api/v1/bond/market-data/date";
const CACHE_FILE = path.join(DATA_DIR, "cache", "own_series.json");
const MAX_WINDOW = 88; // market-data/date 单次跨度上限约 3 个月，留余量

export interface ValPoint {
  d: string; // YYYY-MM-DD
  v: number; // 估值收益率 %
}

/** 估值序列取数模式 */
export type SeriesMode = "auto" | "cached" | "refresh";

export interface SeriesStat {
  latest: number | null;
  latestDate: string | null;
  percentile: number | null; // 0-100，当前估值在序列中的分位（≤当前值的观测占比）
  obs: number;
  firstDate: string | null;
  min: number | null;
  max: number | null;
}

interface SeriesEntry {
  u?: string; // 最后尝试拉取时刻（本地时间 YYYY-MM-DDTHH:MM:SS；无数据券 20 点前允许当日重试）
  s?: string; // 拉取起点（覆盖判断用：估值首日通常晚于截标日，不能拿首条估值日期判断覆盖）
  vals?: ValPoint[];
}
type SeriesCache = Record<string, SeriesEntry>;

function readCache(): SeriesCache {
  try {
    if (!fs.existsSync(CACHE_FILE)) return {};
    return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) as SeriesCache;
  } catch {
    return {};
  }
}

function writeCache(cache: SeriesCache) {
  try {
    fs.mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache), "utf8");
  } catch {
    /* 缓存失败不阻塞 */
  }
}

export function todayStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function addDays(date: string, n: number): string {
  const d = new Date(date + "T12:00:00");
  d.setDate(d.getDate() + n);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 本地当前时刻 YYYY-MM-DDTHH:MM:SS（缓存时间戳用，字符串比较即时间比较） */
function nowLocal(): string {
  const d = new Date();
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 清洗个券代码：去掉 Excel 链接残留前缀（如 q26061240.IB），无后缀时按位数推断 */
export function cleanOwnCode(c: unknown): string | null {
  let s = String(c ?? "").trim();
  if (!s) return null;
  s = s.replace(/^[A-Za-z]+/, "");
  if (!/^\d+(\.(IB|SH|SZ))?$/.test(s)) return null;
  if (!s.includes(".")) s += s.length >= 8 ? ".IB" : ".SH";
  return s;
}

/** 单行估值：银行间(.IB) 中债优先，交易所中证优先；含权(yte)一律优先于到期(ytm) */
export function rowVal(r: Record<string, unknown>, code: string): number | null {
  const pickv = (ytm: unknown, yte: unknown): number | null => {
    for (const x of [yte, ytm]) {
      if (x !== null && x !== undefined && String(x).trim() !== "" && !Number.isNaN(Number(x))) return Number(x);
    }
    return null;
  };
  const cb = pickv(r.cb_ytm, r.cb_yte);
  const cs = pickv(r.cs_ytm, r.cs_yte);
  if (code.endsWith(".IB")) return cb ?? cs;
  return cs ?? cb;
}

export function mergeVals(prev: ValPoint[], got: ValPoint[], start: string): ValPoint[] {
  const m = new Map<string, number>();
  for (const p of prev) if (p.d >= start) m.set(p.d, p.v);
  for (const p of got) m.set(p.d, p.v);
  return [...m.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([d, v]) => ({ d, v }));
}

/** 序列统计：最新值/日期 + 发行以来分位数 + 观测数/极值 */
export function statOf(vals: ValPoint[]): SeriesStat {  if (!vals.length) {
    return { latest: null, latestDate: null, percentile: null, obs: 0, firstDate: null, min: null, max: null };
  }
  const last = vals[vals.length - 1];
  const vs = vals.map((p) => p.v);
  const le = vs.filter((v) => v <= last.v).length;
  return {
    latest: last.v,
    latestDate: last.d,
    percentile: vs.length >= 2 ? Math.round((le / vs.length) * 100) : null,
    obs: vs.length,
    firstDate: vals[0].d,
    min: Math.min(...vs),
    max: Math.max(...vs),
  };
}

/**
 * 缓存元信息（供页面展示「缓存于 X · 估值截至 Y」，不触发任何 DM 请求）：
 *   maxLatestDate = 给定个券中最后估值日的最大值；lastUpdated = 最近一次缓存写入时刻。
 */
export function seriesCacheMeta(codes: (string | null | undefined)[]): {
  maxLatestDate: string | null;
  lastUpdated: string | null;
} {
  const cache = readCache();
  let maxD: string | null = null;
  let lastU: string | null = null;
  for (const c of codes) {
    if (!c) continue;
    const e = cache[c];
    if (!e) continue;
    const vals = Array.isArray(e.vals) ? e.vals : [];
    const d = vals.length ? vals[vals.length - 1].d : null;
    if (d && (!maxD || d > maxD)) maxD = d;
    if (e.u && (!lastU || e.u > lastU)) lastU = e.u;
  }
  return { maxLatestDate: maxD, lastUpdated: lastU };
}

/**
 * 批量获取个券估值序列（带磁盘缓存，按日增量续拉）。
 * items: code + start（该券的观测起点，如截标日/上市日；会自动夹到 3 个月窗口内）。
 * mode:
 *   - "cached"  缓存优先：已有缓存的券一律不再请求 DM（页面日常打开用），
 *               仅对「从未缓存」或「今日尚未尝试过且无数据」的券补拉一次；
 *   - "auto"    缓存不新鲜（最后估值日 < 今天）才增量拉取；
 *   - "refresh" 忽略缓存全量重拉（手动「强制刷新估值」/ 每日 09:30 定时任务用）。
 * 说明（2026-09-11 用户要求）：spread 板块不再每次打开都强制刷新——
 *   每日 09:30 由计划任务调用 ?refresh=1 刷一次，其余打开动作读缓存。
 */
export async function fetchSeriesMap(
  items: { code: string; start?: string | null }[],
  opts: { refresh?: boolean; mode?: SeriesMode } = {}
): Promise<Map<string, ValPoint[]>> {
  const mode: SeriesMode = opts.mode ?? (opts.refresh ? "refresh" : "auto");
  const cache = readCache();
  const today = todayStr();
  const floor = addDays(today, -MAX_WINDOW);

  // 计算每只券需要补拉的区间。
  // ⚠️ 新鲜判断不能用「今天拉过就算」：DM 当日估值晚间才发布，白天拉过会把缓存锁死在昨日。
  //    有数据的券：最后估值日 >= 今天 才算新鲜；无数据的券：20 点前允许当日重试。
  const need = new Map<string, { start: string; from: string }>();
  for (const it of items) {
    const code = it.code;
    if (!code) continue;
    const start0 = it.start && /^\d{4}-\d{2}-\d{2}$/.test(it.start) ? it.start : floor;
    const start = start0 < floor ? floor : start0;
    const e = cache[code];
    const vals = e && Array.isArray(e.vals) ? e.vals : [];
    const lastD = vals.length ? vals[vals.length - 1].d : null;
    const coversStart = !!e?.s && e.s <= start;
    const noDataFresh = !lastD && !!e?.u && e.u >= `${today}T20:00`;
    if (mode === "cached") {
      // 有数据即用缓存；无数据但今天已尝试过也跳过（避免每次打开都打 DM）
      const triedToday = !!e?.u && e.u >= `${today}T00:00`;
      if (e && (lastD || triedToday)) continue;
    } else if (mode === "auto") {
      const fresh = lastD ? lastD >= today : noDataFresh;
      if (coversStart && fresh) continue;
    }
    let from = start;
    if (mode !== "refresh" && coversStart && lastD && lastD >= from) {
      from = addDays(lastD, 1); // 增量：从最后估值日次日起
    }
    if (from > today) continue;
    need.set(code, { start, from });
  }

  // 按 from 分组后每批 5 只拉取
  const groups = new Map<string, string[]>();
  for (const [code, n] of need) {
    const g = groups.get(n.from);
    if (g) g.push(code);
    else groups.set(n.from, [code]);
  }
  for (const [from, codes] of groups) {
    for (let i = 0; i < codes.length; i += 5) {
      const chunk = codes.slice(i, i + 5);
      let rows: Array<Record<string, unknown>> = [];
      try {
        // ⚠️ DM 接口要求 camelCase 参数名（postData 不转换请求体；响应键由 postData 统一 snake 化）
        const resp = await postData<unknown>(
          {
            securityIdList: chunk,
            dataSourceList: [2],
            startDate: from,
            endDate: today,
            fieldNames: ["securityId", "issueDate", "cbYtm", "cbYte", "csYtm", "csYte"],
          },
          MARKET_DATE
        );
        rows = (Array.isArray(resp) ? resp : ((resp as { list?: unknown[] })?.list ?? [])) as Array<
          Record<string, unknown>
        >;
      } catch {
        rows = [];
      }
      const byCode = new Map<string, ValPoint[]>();
      for (const r of rows) {
        const sid = String(r.security_id ?? "");
        const d = String(r.issue_date ?? "");
        if (!sid || !/^\d{4}-\d{2}-\d{2}$/.test(d)) continue;
        const v = rowVal(r, sid);
        if (v === null) continue;
        const arr = byCode.get(sid);
        if (arr) arr.push({ d, v });
        else byCode.set(sid, [{ d, v }]);
      }
      for (const code of chunk) {
        const n = need.get(code)!;
        const got = byCode.get(code) ?? [];
        cache[code] = { u: nowLocal(), s: n.start, vals: mergeVals(cache[code]?.vals ?? [], got, n.start) };
      }
    }
  }
  if (need.size) {
    // 与磁盘现有内容合并后再写，避免并发路由互相覆盖
    const cur = readCache();
    for (const code of need.keys()) cur[code] = cache[code];
    writeCache(cur);
  }

  const out = new Map<string, ValPoint[]>();
  for (const it of items) {
    if (!it.code) continue;
    const vals = (cache[it.code]?.vals ?? []).filter((p) => p.d >= (it.start && /^\d{4}-/.test(it.start) ? it.start : ""));
    if (vals.length) out.set(it.code, vals);
  }
  return out;
}
