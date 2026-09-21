import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { postData, DM_PATHS } from "@/lib/dm-client";

/** 主体评级缓存：{ 主体全称: { external, yy, agency, yyQueriedAt } } */
interface RatingEntry {
  external?: string; // 外部评级（AAA/AA+）
  yy?: string; // YY 评分（7-/6+）
  agency?: string;
  yyQueriedAt?: string; // YY 查询哨兵（YYYY-MM-DD）：DM 无 YY 行时记录当日已查，避免反复请求；YY 行不定期补充，次日自动重查
}
type RatingCache = Record<string, RatingEntry>;

const CACHE_FILE = path.join(DATA_DIR, "cache", "ratings.json");

function readCache(): RatingCache {
  try {
    if (!fs.existsSync(CACHE_FILE)) return {};
    return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) as RatingCache;
  } catch {
    return {};
  }
}

function writeCache(cache: RatingCache) {
  try {
    fs.mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 1), "utf8");
  } catch {
    /* 缓存失败不阻塞主流程 */
  }
}

/**
 * 按主体全称批量获取评级（DM company/rating/data，单次最多 5 个主体）
 * 一次查询同时解析：外部评级（字母开头 AAA/AA+）与 YY 评分（dataSource=YY评分 行，数字开头），各取最新一条
 * 结果写入本地缓存 data/cache/ratings.json：
 *  - 有 YY 的条目长期复用（评级变动慢）
 *  - 无 YY 的条目记当日哨兵，当日不再查、次日自动重查（DM 的 YY 行不定期补充——2026-09 实测部分主体先无后有）
 */
export async function getCompanyRatings(names: string[]): Promise<Record<string, RatingEntry>> {
  const today = new Date().toISOString().slice(0, 10);
  const cache = readCache();
  const result: Record<string, RatingEntry> = {};
  const missing: string[] = [];

  for (const n of names) {
    if (!n) continue;
    const key = n.trim();
    const entry = cache[key];
    // 命中条件：有 YY（长期有效）或 当日已查过 YY（哨兵去重）
    if (entry && (entry.yy || entry.yyQueriedAt === today)) {
      result[key] = entry;
    } else if (!missing.includes(key)) {
      missing.push(key);
    }
  }

  // 分批查 DM（每批最多 5 个）
  for (let i = 0; i < missing.length; i += 5) {
    const chunk = missing.slice(i, i + 5);
    try {
      const rows = (await postData<unknown[]>(
        { comChiNameList: chunk },
        DM_PATHS.companyRating
      )) as Array<{
        com_chi_name?: string;
        rating?: string | null;
        rating_date?: string | null;
        rating_institution_short_name?: string | null;
      }>;
      const byName: Record<string, { external?: string; extDate?: string; yy?: string; yyDate?: string; agency?: string }> = {};
      for (const r of rows) {
        const rating = String(r.rating ?? "").trim();
        if (!rating) continue;
        const name = r.com_chi_name?.trim();
        if (!name) continue;
        const date = r.rating_date ?? "";
        const cur = byName[name] ?? (byName[name] = {});
        // 外部评级 = 字母开头（AAA/AA+/BBB-）；YY 评分 = 数字开头（7-/6+，dataSource=YY评分）
        if (/^[A-Za-z]{1,3}[+-]?$/.test(rating)) {
          if (!cur.external || date > (cur.extDate ?? "")) {
            cur.external = rating;
            cur.extDate = date;
            cur.agency = r.rating_institution_short_name ?? "";
          }
        } else if (/^[0-9]/.test(rating)) {
          if (!cur.yy || date > (cur.yyDate ?? "")) {
            cur.yy = rating;
            cur.yyDate = date;
          }
        }
      }
      for (const n of chunk) {
        const found = byName[n];
        if (found && (found.external || found.yy)) {
          const entry: RatingEntry = { external: found.external, yy: found.yy, agency: found.agency };
          if (!entry.yy) entry.yyQueriedAt = today; // DM 无 YY 行：当日哨兵，次日重查
          result[n] = entry;
          cache[n] = entry;
        }
      }
    } catch {
      /* 单批失败跳过，下次再查 */
    }
  }

  writeCache(cache);
  return result;
}

// ==================== 本地 YY 映射兜底 ====================

const jsonCache = new Map<string, { mtime: number; data: unknown }>();

/** mtime 缓存的 JSON 读取（文件被上传/同步管道更新后自动重读） */
function mtimeJson<T>(rel: string, fallback: T): T {
  const p = path.join(DATA_DIR, rel);
  try {
    const st = fs.statSync(p);
    const hit = jsonCache.get(rel);
    if (!hit || hit.mtime !== st.mtimeMs) {
      const data: unknown = JSON.parse(fs.readFileSync(p, "utf8"));
      jsonCache.set(rel, { mtime: st.mtimeMs, data });
      return data as T;
    }
    return hit.data as T;
  } catch {
    return fallback;
  }
}

/**
 * 本地 YY 兜底链：yy_lookup.json（每日 Excel「YY评分」列历史归集，上传时增量更新）
 * → issuer_ratings_live.json（服务器每晚 DM 刷新，见 dm-refresh.ts）
 * → issuer_ratings.json（同事管道 pack_snapshot 同步的发行人评级归集）。
 * 三份都是"主体全称 → YY"的本地映射，返回 null 表示本地也无。
 */
export function localYyOf(issuer: string | null | undefined): string | null {
  const key = String(issuer ?? "").trim();
  if (!key) return null;
  const yy = mtimeJson<{ map?: Record<string, string> }>("yy_lookup.json", {}).map?.[key];
  if (yy) return yy;
  const live = mtimeJson<{ map?: Record<string, { yy?: string | null }> }>("cache/issuer_ratings_live.json", {}).map?.[key];
  if (live?.yy) return live.yy;
  const ir = mtimeJson<{ map?: Record<string, { yy?: string | null }> }>("cache/issuer_ratings.json", {}).map?.[key];
  return ir?.yy ?? null;
}
