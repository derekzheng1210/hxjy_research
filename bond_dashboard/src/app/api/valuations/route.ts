import { DATA_DIR } from "@/lib/data-dir";
import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/valuations — 返回个券估值（来自 data/valuations.json）
export async function GET() {
  try {
    const p = path.join(DATA_DIR, "valuations.json");
    if (!fs.existsSync(p)) {
      return NextResponse.json({ bonds: {}, error: "缺少 data/valuations.json" });
    }
    const data = JSON.parse(fs.readFileSync(p, "utf8")) as {
      meta: { date: string };
      bonds: Record<string, { security_id?: string | null; cb?: number | null; cs?: number | null; market?: string | null }>;
    };
    return NextResponse.json({ date: data.meta?.date, bonds: data.bonds ?? {} });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "读取失败", bonds: {} }, { status: 500 });
  }
}
