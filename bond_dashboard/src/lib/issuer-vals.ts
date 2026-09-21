// 发行人个券子表的估值补齐：最新估值 + 发行以来分位数（与 spread 页同一口径/同一缓存）。
// 数据源：DM bond/market-data/date（单次跨度 ≤3 个月、每批 5 券），行取值/市场优先级复用 postlist.rowVal。
// 缓存：复用 data/cache/own_series.json（{ code: { u, s, vals:[{d,v}] } }），与 spread 页互相共享增量：
//   - 展开发行人时，已有覆盖序列的券：新鲜（最后估值日 >= 上一工作日）直接用；否则从最后估值日次日增量补；
//   - 序列未覆盖发行日的券：从发行日(截标日)起按 88 天窗口分批回补，结果落盘，之后展开不再重复请求；
//   - 从无数据的券（未上市/取消发行）：允许每日重试一次（当日 20 点后不再重试）。
import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { postData } from "@/lib/dm-client";
import {
  statOf,
  todayStr,
  addDays,
  rowVal,
  mergeVals,
  type SeriesStat,
  type ValPoint,
} from "@/lib/postlist";

const MARKET_DATE = "/dm-quant-func-service/api/v1/bond/market-data/date";
const CACHE_FILE = path.join(DATA_DIR, "cache", "own_series.json");
const WINDOW = 88; // market-data/date 单次跨度上限约 3 个月，留余量
const CONCURRENCY = 4; // 回补请求并发数（控制 429 风险）

interface SeriesEntry {
  u?: string;
  s?: string;
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

function nowLocal(): string {
  const d = new Date();
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 上一工作日（仅剔除周末；节假日多打一次空请求无害） */
function prevWorkday(): string {
  const d = new Date();
  do {
    d.setDate(d.getDate() - 1);
  } while (d.getDay() === 0 || d.getDay() === 6);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 估值新鲜：最后估值日 >= 上一工作日（当日估值 DM 晚间才发布，白天视为已新鲜即可） */
function isFresh(lastD: string | null): boolean {
  return !!lastD && lastD >= prevWorkday();
}

export interface BondValStat {
  latest: number | null;
  latestDate: string | null;
  pct: number | null; // 发行以来分位数 0-100
  obs: number;
}

interface FetchCall {
  chunk: string[];
  from: string;
  to: string;
}

async function fetchRows(chunk: string[], from: string, to: string): Promise<Array<Record<string, unknown>>> {
  try {
    // ⚠️ DM 接口要求 camelCase 参数名（postData 不转换请求体）
    const resp = await postData<unknown>(
      {
        securityIdList: chunk,
        dataSourceList: [2],
        startDate: from,
        endDate: to,
        fieldNames: ["securityId", "issueDate", "cbYtm", "cbYte", "csYtm", "csYte"],
      },
      MARKET_DATE
    );
    return (Array.isArray(resp) ? resp : ((resp as { list?: unknown[] })?.list ?? [])) as Array<
      Record<string, unknown>
    >;
  } catch {
    return [];
  }
}

/**
 * 为一组个券（code + 观测起点 start=截标日）确保估值序列可用，返回每只券的统计。
 * 首次展开某发行人时会有一次性回补请求（并发 4、按窗口分批），结果全部落盘 own_series.json。
 */
export async function ensureBondStats(
  items: { code: string; start: string }[]
): Promise<Map<string, BondValStat>> {
  const cache = readCache();
  const today = todayStr();
  const floor = addDays(today, -WINDOW);

  // ── 1. 逐券判定缺口，生成回补窗口 ──
  const startOf = new Map<string, string>();
  const windowsByCode = new Map<string, Array<{ from: string; to: string }>>();
  const seen = new Set<string>();

  for (const it of items) {
    const code = it.code;
    if (!code || seen.has(code)) continue;
    seen.add(code);
    const start = /^\d{4}-\d{2}-\d{2}$/.test(it.start) ? it.start : addDays(today, -365);
    startOf.set(code, start);
    if (start > today) continue; // 尚未缴款的券，无估值可言

    const e = cache[code];
    const vals = e && Array.isArray(e.vals) ? e.vals : [];
    const s = e?.s;
    const lastD = vals.length ? vals[vals.length - 1].d : null;
    const wins: Array<{ from: string; to: string }> = [];

    if (!lastD) {
      // 从无数据：允许每日重试一次（当日 20 点后不再重试）
      const noDataFresh = !!e?.u && e.u >= `${today}T20:00`;
      if (!noDataFresh) {
        const from = start > floor ? start : floor; // 未上市券只探近 3 个月
        wins.push({ from, to: today });
      }
    } else if (s && s <= start) {
      // 序列已覆盖发行日以来：不新鲜则增量补
      if (!isFresh(lastD) && addDays(lastD, 1) <= today) {
        wins.push({ from: addDays(lastD, 1), to: today });
      }
    } else {
      // 序列未覆盖发行日：从发行日起按窗口回补（与已有数据重叠部分合并即可）
      let from = start;
      let guard = 0;
      while (from <= today && guard++ < 12) {
        const to0 = addDays(from, WINDOW);
        wins.push({ from, to: to0 > today ? today : to0 });
        from = addDays(to0, 1);
      }
    }
    if (wins.length) windowsByCode.set(code, wins);
  }

  // ── 2. 按窗口起点分组、每批 5 券生成请求（仅对有缺口的券） ──
  const groups = new Map<string, string[]>();
  for (const [code, wins] of windowsByCode) {
    for (const w of wins) {
      const g = groups.get(w.from);
      if (g) g.push(code);
      else groups.set(w.from, [code]);
    }
  }
  const calls: FetchCall[] = [];
  for (const [from, codes] of groups) {
    const uniq = [...new Set(codes)];
    for (let i = 0; i < uniq.length; i += 5) calls.push({ chunk: uniq.slice(i, i + 5), from, to: today });
  }

  // ── 3. 并发执行请求，收集结果 ──
  const got = new Map<string, ValPoint[]>();
  let idx = 0;
  const worker = async () => {
    while (idx < calls.length) {
      const c = calls[idx++];
      const rows = await fetchRows(c.chunk, c.from, c.to);
      for (const r of rows) {
        const sid = String(r.security_id ?? "");
        const d = String(r.issue_date ?? "");
        if (!sid || !/^\d{4}-\d{2}-\d{2}$/.test(d)) continue;
        const v = rowVal(r, sid);
        if (v === null) continue;
        const arr = got.get(sid);
        if (arr) arr.push({ d, v });
        else got.set(sid, [{ d, v }]);
      }
    }
  };
  if (calls.length) {
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, calls.length) }, worker));
  }

  // ── 4. 合并落盘（先读磁盘最新内容，避免覆盖 spread 页写入的其他券） ──
  if (calls.length) {
    const cur = readCache();
    for (const code of windowsByCode.keys()) {
      const start = startOf.get(code)!;
      const prev = cur[code];
      const prevS = prev?.s;
      // 序列起点：已有更早覆盖则保留；否则声明为发行日（保证增量/回补判定稳定）
      const sNew = prevS && prevS <= start ? prevS : start;
      cur[code] = {
        u: nowLocal(),
        s: sNew,
        vals: mergeVals(prev?.vals ?? [], got.get(code) ?? [], start),
      };
    }
    writeCache(cur);
    for (const [code, entry] of Object.entries(cur)) cache[code] = entry;
  }

  // ── 5. 统计输出（只统计发行日以来的观测） ──
  const out = new Map<string, BondValStat>();
  for (const [code, start] of startOf) {
    const vals = (cache[code]?.vals ?? []).filter((p) => p.d >= start);
    const st: SeriesStat = statOf(vals);
    out.set(code, {
      latest: st.latest,
      latestDate: st.latestDate,
      pct: st.percentile,
      obs: st.obs,
    });
  }
  return out;
}
