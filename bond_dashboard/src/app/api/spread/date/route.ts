import { NextRequest, NextResponse } from "next/server";
import { fetchPrimaryAll } from "@/lib/dm-client";
import { cleanReason, sameBookDate } from "@/lib/credit";
import { cleanOwnCode, fetchSeriesMap, statOf, todayStr } from "@/lib/postlist";
import { internalRatingOf } from "@/lib/internal-ratings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/spread/date?date=YYYY-MM-DD[&refresh=1]
// 当日发行个券：发行票面 vs 上市后最新估值 + 发行以来估值分位数
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const date = sp.get("date") || todayStr();
  const refresh = sp.get("refresh") === "1";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: "日期格式应为 YYYY-MM-DD" }, { status: 400 });
  }
  try {
    const rows = await fetchPrimaryAll(date, date, 1);
    // 与全站口径一致：仅截标日=当日 + 剔永续/PPN/私募/转债/可交换，且需有票面
    const day = sameBookDate(rows as { subscribe_date?: string | null }[], date)
      .filter((b) => !cleanReason(b as Parameters<typeof cleanReason>[0]))
      .filter((b) => (b as { issue_yield?: number | null }).issue_yield !== null && (b as { issue_yield?: number | null }).issue_yield !== undefined);

    const items = day.map((raw) => {
      const b = raw as Record<string, unknown>;
      return {
        name: String(b.sec_short_name ?? ""),
        code: cleanOwnCode(b.security_id),
        tenor: String(b.bond_issue_tenor ?? "") || null,
        matu: String(b.bond_matu_struct ?? "") || null,
        coupon: b.issue_yield !== null && b.issue_yield !== undefined ? Number(b.issue_yield) : null,
        yy: (b.issuer_yy as string | null) ?? null,
        start: (b.subscribe_date as string | null) ?? null,
        planYi: b.plan_issue_amount != null ? Number(b.plan_issue_amount) / 10000 : null,
        // 主体内评（信评门户数据，按发行人全称匹配）
        internalRating: internalRatingOf(b.issuer_full_name as string | null) || null,
      };
    });

    const series = await fetchSeriesMap(
      items.filter((i) => i.code).map((i) => ({ code: i.code as string, start: i.start })),
      { refresh }
    );

    const bonds = items.map((i) => {
      const st = statOf(series.get(i.code ?? "") ?? []);
      return {
        ...i,
        latest: st.latest,
        latestDate: st.latestDate,
        percentile: st.percentile,
        obs: st.obs,
        firstDate: st.firstDate,
        spreadBp: st.latest !== null && i.coupon !== null ? (st.latest - i.coupon) * 100 : null,
      };
    });
    // 按利差降序（无估值的沉底）
    bonds.sort((a, b) => (b.spreadBp ?? -Infinity) - (a.spreadBp ?? -Infinity));

    return NextResponse.json({
      date,
      bonds,
      meta: {
        total: bonds.length,
        valued: bonds.filter((b) => b.spreadBp !== null).length,
        generated: new Date().toISOString(),
      },
    });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "加载失败" }, { status: 500 });
  }
}
