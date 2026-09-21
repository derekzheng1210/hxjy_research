import { DATA_DIR } from "@/lib/data-dir";
import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ROOT = process.cwd();
const POSTERS_DIR = path.join(DATA_DIR, "posters");

// GET /api/poster/file?date=YYYY-MM-DD&type=png|html
export async function GET(req: NextRequest) {
  const date = req.nextUrl.searchParams.get("date") || "";
  const type = req.nextUrl.searchParams.get("type") === "html" ? "html" : "png";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: "缺少日期" }, { status: 400 });
  }
  const file = path.join(POSTERS_DIR, `每日一级发行结果汇总_${date}.${type}`);
  if (!fs.existsSync(file)) {
    return NextResponse.json({ error: "海报不存在", file: path.basename(file) }, { status: 404 });
  }
  const buf = fs.readFileSync(file);
  return new NextResponse(buf, {
    headers: {
      "Content-Type": type === "png" ? "image/png" : "text/html; charset=utf-8",
      "Cache-Control": "no-cache",
      "Content-Length": String(buf.length),
    },
  });
}
