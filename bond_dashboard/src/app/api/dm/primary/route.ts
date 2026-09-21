import { NextRequest, NextResponse } from "next/server";
import { fetchPrimaryAll, postData, DM_PATHS } from "@/lib/dm-client";
import { getCompanyRatings, localYyOf } from "@/lib/ratings";
import { isPPN, sameBookDate } from "@/lib/credit";
import { enrichSimilarValuation } from "@/lib/marketval";
import { internalRatingOf } from "@/lib/internal-ratings";
import type { PrimaryResult } from "@/lib/dm-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** 剔除 PPN（定向工具）——全站口径统一，禁止 PPN 进入任何板块 */
function excludePPN<T extends { bond_type_desc?: string | null; sec_short_name?: string | null }>(rows: T[]): T[] {
  return rows.filter((b) => !isPPN(b));
}

// GET /api/dm/primary?start=2026-09-01&end=2026-09-01&category=1&page=1&rating=1
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const start = sp.get("start");
    const end = sp.get("end");
    const category = Number(sp.get("category") ?? 1);
    const withRating = sp.get("rating") === "1";
    if (!start || !end) {
      return NextResponse.json({ error: "缺少 start/end 参数" }, { status: 400 });
    }

    // 单页查询（前端可翻页展示）
    const page = Math.max(1, Number(sp.get("page") ?? 1));
    const offset = (page - 1) * 100;
    const res = await postData<PrimaryResult>(
      { startDate: start, endDate: end, bondCategory: category, offset },
      DM_PATHS.primary
    );
    const rawList = res?.list || [];
    let list = excludePPN(rawList);

    // 单日查询：DM 接口会额外返回上一交易日簿记的券（实测 8-11 查询混入 8-10×22、8-07×1），
    // 按截标日（subscribe_date）精确对齐查询日，与每日《一级发行-信用债发行》Excel 同口径。
    let dateFiltered = 0;
    if (start === end) {
      const before = list.length;
      list = sameBookDate(list, start);
      dateFiltered = before - list.length;
    }

    // 可比券估值行权化（含权可比券取行权收益率后覆写，非含权不变）
    await enrichSimilarValuation(list);

    // 主体内评富化（信评门户数据，按发行人全称匹配）
    for (const b of list) {
      const ir = internalRatingOf(b.issuer_full_name as string | null | undefined);
      if (ir) (b as Record<string, unknown>).internalRating = ir;
    }

    // 按需补主体评级（按发行人全称去重后查询）
    if (withRating && list.length) {
      const names = [...new Set(list.map((b) => String(b.issuer_full_name ?? "").trim()).filter(Boolean))];
      const ratingMap = await getCompanyRatings(names);
      for (const b of list) {
        const name = String(b.issuer_full_name ?? "").trim();
        const info = ratingMap[name];
        if (info?.yy) {
          (b as Record<string, unknown>).issuer_yy = info.yy;
        } else if (!(b as Record<string, unknown>).issuer_yy && localYyOf(name)) {
          // DM 无 YY 时，用本地映射（yy_lookup + issuer_ratings）补齐
          (b as Record<string, unknown>).issuer_yy = localYyOf(name);
        }
      }
    }
    return NextResponse.json({
      list,
      max_offset: res?.max_offset ?? res?.maxOffset ?? 0,
      page,
      // 口径元数据（便于前端展示与对账）
      bookDate: start === end ? start : null,
      rawCount: rawList.length,
      dateFiltered,
    });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "DM API 调用失败" }, { status: 500 });
  }
}

// GET /api/dm/primary/all?start=...&end=...&category=1 （自动翻页拉全量）
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { start, end, category = 1 } = body as { start: string; end: string; category?: number };
    if (!start || !end) {
      return NextResponse.json({ error: "缺少 start/end" }, { status: 400 });
    }
    const rows = excludePPN(await fetchPrimaryAll(start, end, category));
    await enrichSimilarValuation(rows);
    // 主体内评富化（信评门户数据，按发行人全称匹配）
    for (const b of rows) {
      const ir = internalRatingOf(b.issuer_full_name as string | null | undefined);
      if (ir) (b as Record<string, unknown>).internalRating = ir;
    }
    return NextResponse.json({ list: rows, count: rows.length });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "DM API 调用失败" }, { status: 500 });
  }
}
