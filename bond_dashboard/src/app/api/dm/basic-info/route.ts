import { NextRequest, NextResponse } from "next/server";
import { fetchBasicInfo } from "@/lib/dm-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// POST /api/dm/basic-info  body: { securityIds: string[] }
// 返回债券基础资料（含 list_date 上市日）
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const ids: string[] = body?.securityIds ?? body?.security_id_list ?? [];
    if (!ids.length) {
      return NextResponse.json({ error: "缺少 securityIds" }, { status: 400 });
    }
    // 分批（每批 20 个）
    const out: Record<string, unknown>[] = [];
    for (let i = 0; i < ids.length; i += 20) {
      const chunk = ids.slice(i, i + 20);
      const rows = await fetchBasicInfo(chunk);
      out.push(...rows);
    }
    return NextResponse.json({ list: out });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "基础资料获取失败" }, { status: 500 });
  }
}
