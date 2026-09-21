import { DATA_DIR } from "@/lib/data-dir";
import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { internalRatingOf } from "@/lib/internal-ratings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/recommended — 返回推荐个券（来自 data/recommended.json）
export async function GET() {
  try {
    const p = path.join(DATA_DIR, "recommended.json");
    if (!fs.existsSync(p)) {
      return NextResponse.json({ list: [], error: "缺少 data/recommended.json" });
    }
    // 票面缺失原因标注（取消发行 / 回拨X年 / 未截标），scripts/build_bond_notes.py 生成
    let notes: Record<string, { note?: string }> = {};
    try {
      const np = path.join(DATA_DIR, "store", "bond_notes.json");
      notes = JSON.parse(fs.readFileSync(np, "utf8")) as Record<string, { note?: string }>;
    } catch {
      notes = {};
    }
    const list = (JSON.parse(fs.readFileSync(p, "utf8")) as Record<string, unknown>[]).map((x) => {
      const name = String(x.name ?? "");
      const coupon = Number(x.coupon ?? 0);
      const note = coupon > 0 ? undefined : notes[name]?.note;
      const withNote = note ? { ...x, couponNote: note } : x;
      // 主体内评富化（信评门户数据，按发行人全称匹配）
      const internalRating = internalRatingOf(x.issuer as string | null) || null;
      return internalRating ? { ...withNote, internalRating } : { ...withNote, internalRating: null };
    });
    // 全站口径：剔除 PPN/定向工具
    const clean = list.filter((x) => {
      const o = x as { bond_type?: string | null; type?: string | null; name?: string | null };
      const t = String(o.bond_type ?? o.type ?? "");
      const n = String(o.name ?? "");
      if (t.includes("PPN") || t.includes("定向")) return false;
      if (n.includes("PPN")) return false;
      return true;
    });
    return NextResponse.json({ list: clean });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "读取失败", list: [] }, { status: 500 });
  }
}
