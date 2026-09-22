import { NextRequest, NextResponse } from "next/server";
import { runDailyRefresh, readRefreshState } from "@/lib/dm-refresh";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/refresh — 查看数据刷新状态（最近一次执行时间/结果）
export async function GET() {
  return NextResponse.json(readRefreshState());
}

// POST /api/refresh — 手动触发一轮 DM 数据刷新（每日 19:00 自动任务不受影响）
// 可选 ?ratingsLimit=N — 发行人评级本轮仅刷前 N 家（全量默认 0）
// 可选 ?steps=a,b — 仅跑指定步骤（calendar/bids/valuations/gov30y/issuerRatings/couponCurve/issuerYtd），
// 用于单步补跑（如 issuer_ytd 停更时仅重建发行人分析），不影响当晚自动全量刷新
export async function POST(req: NextRequest) {
  const limit = Number(req.nextUrl.searchParams.get("ratingsLimit") ?? 0) || 0;
  const stepsParam = req.nextUrl.searchParams.get("steps");
  const steps = stepsParam
    ? stepsParam.split(",").map((s) => s.trim()).filter(Boolean)
    : undefined;
  const r = await runDailyRefresh(limit, steps);
  if (r.error) return NextResponse.json({ error: r.error }, { status: 500 });
  if (r.skipped) return NextResponse.json({ ok: true, skipped: true, message: "刷新正在进行中，请稍后查看状态", summary: r.summary });
  return NextResponse.json({ ok: true, steps: steps ?? "all", summary: r.summary });
}
