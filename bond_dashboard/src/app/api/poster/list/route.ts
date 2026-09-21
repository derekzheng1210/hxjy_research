import { DATA_DIR } from "@/lib/data-dir";
import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ROOT = process.cwd();
const POSTERS_DIR = path.join(DATA_DIR, "posters");

interface PosterEntry {
  date: string;
  pngSize: number;
  hasHtml: boolean;
}

// GET /api/poster/list → 已有海报（按日期倒序）
export async function GET() {
  const out: PosterEntry[] = [];
  try {
    if (!fs.existsSync(POSTERS_DIR)) {
      return NextResponse.json({ list: [] });
    }
    const seen = new Map<string, { pngSize: number; hasHtml: boolean }>();
    for (const fn of fs.readdirSync(POSTERS_DIR)) {
      const m = fn.match(/^每日一级发行结果汇总_(\d{4}-\d{2}-\d{2})\.(png|html)$/);
      if (!m) continue;
      const [date, ext] = [m[1], m[2]];
      const cur = seen.get(date) ?? { pngSize: 0, hasHtml: false };
      const size = fs.statSync(path.join(POSTERS_DIR, fn)).size;
      if (ext === "png") cur.pngSize = size;
      if (ext === "html") cur.hasHtml = true;
      seen.set(date, cur);
    }
    for (const [date, info] of seen.entries()) {
      if (info.pngSize > 1000) out.push({ date, pngSize: info.pngSize, hasHtml: info.hasHtml });
    }
  } catch {
    /* ignore */
  }
  out.sort((a, b) => b.date.localeCompare(a.date));
  return NextResponse.json({ list: out, dir: POSTERS_DIR });
}
