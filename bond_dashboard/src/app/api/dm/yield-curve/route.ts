import { NextRequest, NextResponse } from "next/server";
import { fetchYieldCurve } from "@/lib/dm-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/dm/yield-curve?start=2026-09-01&end=2026-09-01&curve=中债国债收益率曲线&terms=1,3,5,7,10
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const start = sp.get("start") ?? "";
    const end = sp.get("end") ?? "";
    const curve = sp.get("curve") ?? "中债国债收益率曲线";
    const terms = (sp.get("terms") ?? "1,3,5,7,10").split(",");
    if (!start || !end) {
      return NextResponse.json({ error: "缺少 start/end 参数" }, { status: 400 });
    }
    const rows = await fetchYieldCurve(start, end, curve, terms);
    return NextResponse.json({ list: rows });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "收益率曲线获取失败" }, { status: 500 });
  }
}
