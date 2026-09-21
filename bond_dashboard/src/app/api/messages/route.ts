import { NextRequest, NextResponse } from "next/server";
import { getMessages, addMessage, deleteMessage } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/messages （倒序：最新在前）
export async function GET() {
  const messages = getMessages().sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return NextResponse.json({ list: messages });
}

// POST /api/messages  body: { author, content }
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const content = String(body?.content ?? "").trim();
    if (!content) {
      return NextResponse.json({ error: "内容不能为空" }, { status: 400 });
    }
    if (content.length > 2000) {
      return NextResponse.json({ error: "内容过长（限 2000 字）" }, { status: 400 });
    }
    const author = String(body?.author ?? "匿名").trim().slice(0, 20) || "匿名";
    const record = addMessage({ author, content });
    return NextResponse.json({ record }, { status: 201 });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "发布失败" }, { status: 500 });
  }
}

// DELETE /api/messages?id=xxx
export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "缺少 id" }, { status: 400 });
  const ok = deleteMessage(id);
  if (!ok) return NextResponse.json({ error: "留言不存在" }, { status: 404 });
  return NextResponse.json({ ok: true });
}
