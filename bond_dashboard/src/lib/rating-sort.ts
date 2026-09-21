// 评级 / 期限 排序辅助：把评级字符串转为可用于排序的序号（越小越优/越短），
// 无法解析返回 null（排序时 null 恒排最后）。

/** YY 评级：1~8 档，数字越小越优；同档内 + 优于平级、- 劣于平级（如 4+ > 4 > 4-） */
export function yyRank(v: string | number | null | undefined): number | null {
  const m = /^([1-9])([+-])?$/.exec(String(v ?? "").trim());
  if (!m) return null;
  const mod = m[2] === "+" ? -1 : m[2] === "-" ? 1 : 0;
  return Number(m[1]) * 10 + mod;
}

const RATING_GROUPS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D"];

/** 字母评级（主体内评/外部评级）：AAA 最优 → C；同档内 + 优于平级、- 劣于平级（AA+ > AA > AA-） */
export function ratingRank(v: string | null | undefined): number | null {
  const m = /^([A-D]{1,3})([+-])?$/.exec(String(v ?? "").trim().toUpperCase());
  if (!m) return null;
  const group = RATING_GROUPS.indexOf(m[1]);
  if (group < 0) return null;
  const mod = m[2] === "+" ? -1 : m[2] === "-" ? 1 : 0;
  return group * 10 + mod;
}

/**
 * 期限 → 排序键（数值，越小越短）。仅供排序使用，不是真实年数。
 *  - 主键 = 行权前年数："3Y"/"0.25Y" 按年、"270D" 按 365 折年、"9M" 按 12 折年；
 *    "5+5Y"/"3+2"/"2+N" 等组合期限取首段（行权前，无单位默认按年）；
 *  - 行权前相同时按全额期限细分（如 3Y < 3+2 < 3+3）。
 *  编码：主键×10000 + min(全额期限, 9999)。无法解析返回 null（恒排最后）。
 */
export function termSortKey(v: string | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const s = String(v).trim().toUpperCase();
  if (!s) return null;
  // 逐段解析："5+5Y"→[5,5]、"270D"→[0.74]、("2+N" 的 "N" 段无法解析记 null)
  const segs = s.split("+").map((seg) => {
    const m = /^(\d+(?:\.\d+)?)\s*([YDM年月天日]?)/.exec(seg.trim());
    if (!m) return null;
    const n = Number(m[1]);
    const u = m[2];
    if (u === "D" || u === "天" || u === "日") return n / 365;
    if (u === "M" || u === "月") return n / 12;
    return n; // Y / 年 / 无单位（组合期限的段按年）
  });
  const primary = segs[0];
  if (primary === null || primary === undefined) return null;
  const total = segs.reduce<number>((acc, x) => acc + (x ?? 0), 0);
  return primary * 10000 + Math.min(total, 9999);
}
