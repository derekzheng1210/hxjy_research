import { NextRequest, NextResponse } from "next/server";
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { promisify } from "node:util";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileP = promisify(execFile);
const ROOT = process.cwd();
const PY = "C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Scripts/python.exe";

// POST /api/poster/generate  body:{date?}  生成指定日（缺省=最新收盘版）海报
export async function POST(req: NextRequest) {
  let date = "";
  try {
    const body = await req.json().catch(() => ({}));
    date = String(body?.date ?? "").trim();
  } catch {
    /* ignore */
  }
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: "日期格式应为 YYYY-MM-DD" }, { status: 400 });
  }
  const script = path.join(ROOT, "scripts", "build_daily_poster.py");
  const args = date ? [script, date] : [script];
  if (!fs.existsSync(PY)) {
    return NextResponse.json({ error: "缺少 Python 运行环境" }, { status: 500 });
  }
  try {
    const { stdout } = await execFileP(PY, args, {
      cwd: ROOT,
      timeout: 10 * 60 * 1000,
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    // 成功（exit 0）：脚本最后一行输出 JSON 结果
    let parsed: Record<string, unknown> = {};
    try {
      parsed = JSON.parse(stdout.trim().split("\n").pop() || "{}");
    } catch {
      parsed = { raw: stdout.trim().slice(-500) };
    }
    return NextResponse.json(parsed, { status: parsed?.ok ? 200 : 200 });
  } catch (e) {
    const err = e as { code?: number; stdout?: string; stderr?: string; message?: string };
    // 脚本以非零退出（如无票面列正常跳过 exit=1 / 异常 exit=2）时，
    // 仍会打印一行 JSON 结果 → 优先解析还原原因，避免 500 丢原因。
    const last = (err?.stdout || "").trim().split("\n").filter(Boolean).pop();
    if (last) {
      try {
        const parsed = JSON.parse(last) as Record<string, unknown>;
        return NextResponse.json(parsed, { status: 200 });
      } catch {
        /* 非 JSON 输出，落到通用错误 */
      }
    }
    return NextResponse.json(
      { ok: false, error: err?.message || "海报生成失败", stderrTail: (err?.stderr || "").trim().split("\n").slice(-10) },
      { status: 500 }
    );
  }
}
