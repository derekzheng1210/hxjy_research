import { DATA_DIR } from "@/lib/data-dir";
import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/portfolio-series
export async function GET() {
  try {
    const p = path.join(DATA_DIR, "portfolio_series.json");
    const j = JSON.parse(readFileSync(p, "utf-8"));
    return NextResponse.json(j);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "组合序列加载失败" },
      { status: 500 }
    );
  }
}
