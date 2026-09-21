import { DATA_DIR } from "@/lib/data-dir";
import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { internalRatingOf } from "@/lib/internal-ratings";
import { getCompanyRatings, localYyOf } from "@/lib/ratings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// 日历数据（Excel 清单口径，由 scripts/sync_excel_calendar.py 生成）
interface CalBond {
  name: string;
  amountYi: number;
  type?: string | null;
  tenor?: string | null;
  issuer?: string | null;
  yy?: string | null;
  forecast?: number | null;
  coupon?: number | null;
  payDate?: string | null;
  recommended?: boolean;
}

interface CalDay {
  file: string;
  count: number;
  planYi: number;
  bonds: CalBond[];
}

interface CalFile {
  meta: { source?: string; generated?: string; fileCount?: number };
  days: Record<string, CalDay>;
}

function readCal(): CalFile | null {
  try {
    const p = path.join(DATA_DIR, "excel_calendar.json");
    if (!fs.existsSync(p)) return null;
    return JSON.parse(fs.readFileSync(p, "utf8")) as CalFile;
  } catch {
    return null;
  }
}

// GET /api/excel-calendar?month=YYYY-MM  → 当月已有发行日的汇总（用于日期跳转）
// GET /api/excel-calendar?date=YYYY-MM-DD → 某日完整清单（found/day）
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const month = sp.get("month");
  const date = sp.get("date");
  const cal = readCal();

  if (!cal) {
    return NextResponse.json({ error: "日历数据缺失，请运行 scripts/sync_excel_calendar.py 重新同步" }, { status: 404 });
  }

  if (month) {
    const days = Object.entries(cal.days)
      .filter(([d]) => d.startsWith(month))
      .map(([date, v]) => ({ date, count: v.count, planYi: v.planYi }))
      .sort((a, b) => a.date.localeCompare(b.date));
    return NextResponse.json({ days, generated: cal.meta?.generated ?? "" });
  }

  if (date) {
    const day = cal.days[date];
    if (!day) return NextResponse.json({ found: false });
    let bonds = day.bonds;
    // YY 兜底：清单缺失时（Excel 个别空白 / DM 补数日新主体）按发行人补齐：
    // 本地映射（yy_lookup + issuer_ratings）→ DM 评级接口（带磁盘缓存与当日哨兵）
    const noYy = bonds.filter((b) => !b.yy && b.issuer);
    if (noYy.length) {
      const need = [...new Set(noYy.map((b) => String(b.issuer).trim()))].filter((n) => !localYyOf(n));
      const dm = need.length ? await getCompanyRatings(need) : {};
      bonds = bonds.map((b) =>
        b.yy || !b.issuer
          ? b
          : { ...b, yy: localYyOf(b.issuer) ?? dm[String(b.issuer).trim()]?.yy ?? null }
      );
    }
    // 主体内评富化（信评门户数据，按发行人全称匹配）
    bonds = bonds.map((b) => ({ ...b, internalRating: internalRatingOf(b.issuer) || null }));
    return NextResponse.json({ found: true, day: { ...day, bonds }, generated: cal.meta?.generated ?? "" });
  }

  return NextResponse.json({ meta: cal.meta, dates: Object.keys(cal.days).sort() });
}
