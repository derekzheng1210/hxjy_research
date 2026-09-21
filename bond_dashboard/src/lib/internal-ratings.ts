// 主体内评（信评门户 magicflu 数据）读取层
// 数据来源：研究门户 juyuan_credit_tools_portal 的每日更新任务抓取信评门户后
// 落地的 portal_data.json（ratings 段：主体全称 → 内评级别，约 3700 家主体）。
// 路径由环境变量 INTERNAL_RATINGS_FILE 指定（见 .env.local.example）；
// 未配置或文件缺失/损坏时返回空映射，全站内评列显示"-"（优雅降级，不影响其他功能）。
import fs from "node:fs";

const BLANK = new Set(["#N/A", "NA", "N/A", "-", "--", ""]);

let cachedMtimeMs: number | null = null;
let cachedMap: Record<string, string> = {};

/** 规范化内评级别（与门户 normalize_rating 一致：去空格/大写/空值视为无） */
function normalizeRating(v: unknown): string {
  const t = String(v ?? "").trim().toUpperCase().replace(/\s+/g, "");
  return BLANK.has(t) ? "" : t;
}

/** 主体全称 → 内评级别 映射（mtime 缓存：portal_data.json 被门户任务更新后自动重读，无需重启） */
export function internalRatings(): Record<string, string> {
  const file = process.env.INTERNAL_RATINGS_FILE;
  if (!file) return {};
  try {
    const st = fs.statSync(file);
    if (st.mtimeMs !== cachedMtimeMs) {
      const j = JSON.parse(fs.readFileSync(file, "utf8")) as {
        ratings?: Record<string, unknown>;
      };
      const map: Record<string, string> = {};
      for (const [issuer, rating] of Object.entries(j.ratings ?? {})) {
        const r = normalizeRating(rating);
        if (r) map[issuer.trim()] = r;
      }
      cachedMap = map;
      cachedMtimeMs = st.mtimeMs;
    }
    return cachedMap;
  } catch {
    return {};
  }
}

/** 按主体全称取内评（无则空串） */
export function internalRatingOf(issuer: string | null | undefined): string {
  const key = String(issuer ?? "").trim();
  return key ? (internalRatings()[key] ?? "") : "";
}
