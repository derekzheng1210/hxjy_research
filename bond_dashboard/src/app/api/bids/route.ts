import { DATA_DIR } from "@/lib/data-dir";
import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { getBids, addBid, updateBid, deleteBid } from "@/lib/store";
import { internalRatingOf } from "@/lib/internal-ratings";
import { localYyOf } from "@/lib/ratings";
import type { BidRecord } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** 票面缺失原因标注（取消发行 / 回拨X年 / 未截标），由 scripts/build_bond_notes.py 生成 */
function couponNotes(): Record<string, { note?: string }> {
  try {
    const p = path.join(DATA_DIR, "store", "bond_notes.json");
    return JSON.parse(fs.readFileSync(p, "utf8")) as Record<string, { note?: string }>;
  } catch {
    return {};
  }
}

// GET /api/bids?type=participated|won
export async function GET(req: NextRequest) {
  const type = req.nextUrl.searchParams.get("type");
  let bids = getBids();
  if (type === "participated" || type === "won") {
    bids = bids.filter((b) => b.type === type);
  }
  // 票面为空的记录附加缺失原因标注（取消发行 / 回拨X年 / 未截标）
  const notes = couponNotes();
  bids = bids.map((b) => {
    const note = b.coupon ? undefined : notes[b.bondName]?.note;
    const internalRating = internalRatingOf(b.issuer) || null;
    // YY 补齐：登记表原值优先，缺失时按发行人走本地映射链（yy_lookup → live → issuer_ratings）
    const yy = b.yy ?? localYyOf(b.issuer) ?? undefined;
    return note ? { ...b, couponNote: note, internalRating, yy } : { ...b, internalRating, yy };
  });
  // 按投标日期倒序
  bids.sort((a, b) => (b.bidDate || "").localeCompare(a.bidDate || ""));
  return NextResponse.json({ list: bids });
}

// POST /api/bids
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as Partial<BidRecord>;
    if (!body.bondName || !body.type || body.amount === undefined) {
      return NextResponse.json({ error: "缺少必填字段：bondName/type/amount" }, { status: 400 });
    }
    if (body.type !== "participated" && body.type !== "won") {
      return NextResponse.json({ error: "type 必须为 participated 或 won" }, { status: 400 });
    }
    const record = addBid({
      type: body.type,
      bondName: body.bondName,
      securityId: body.securityId || undefined,
      issuer: body.issuer || undefined,
      term: body.term || undefined,
      amount: Number(body.amount),
      coupon: body.coupon !== undefined ? Number(body.coupon) : undefined,
      spread: body.spread !== undefined ? Number(body.spread) : undefined,
      yy: body.yy || undefined,
      bidDate: body.bidDate || new Date().toISOString().slice(0, 10),
      payDate: body.payDate || undefined,
      listDate: body.listDate || undefined,
      note: body.note || undefined,
    });
    return NextResponse.json({ record }, { status: 201 });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "保存失败" }, { status: 500 });
  }
}

// PATCH /api/bids?id=xxx  或 DELETE /api/bids?id=xxx
export async function PATCH(req: NextRequest) {
  try {
    const id = req.nextUrl.searchParams.get("id");
    if (!id) return NextResponse.json({ error: "缺少 id" }, { status: 400 });
    const patch = (await req.json()) as Partial<BidRecord>;
    const updated = updateBid(id, patch);
    if (!updated) return NextResponse.json({ error: "记录不存在" }, { status: 404 });
    return NextResponse.json({ record: updated });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "更新失败" }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "缺少 id" }, { status: 400 });
  const ok = deleteBid(id);
  if (!ok) return NextResponse.json({ error: "记录不存在" }, { status: 404 });
  return NextResponse.json({ ok: true });
}
