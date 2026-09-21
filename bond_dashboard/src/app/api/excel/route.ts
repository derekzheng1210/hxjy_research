import { NextRequest, NextResponse } from "next/server";
import { importDailyExcel, importBidsExcel } from "@/lib/excel-import";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// POST /api/excel — Excel 上传导入
// multipart/form-data: file=*.xlsx, type=daily(每日发行清单) | bids(一级中标登记表)
export async function POST(req: NextRequest) {
  try {
    const form = await req.formData();
    const type = String(form.get("type") ?? "daily");
    const file = form.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "缺少文件（file 字段）" }, { status: 400 });
    }
    if (!/\.xlsx$/i.test(file.name)) {
      return NextResponse.json({ error: "仅支持 .xlsx 文件" }, { status: 400 });
    }
    const buf = Buffer.from(await file.arrayBuffer());

    if (type === "daily") {
      const result = await importDailyExcel(buf, file.name);
      return NextResponse.json({
        ok: true,
        type,
        fileName: file.name,
        result,
        message: `已导入 ${result.date}：共 ${result.total} 只 / ${result.planYi} 亿，推荐 ${result.recommendedCount} 只${result.dayExisted ? "（覆盖该日旧数据）" : ""}`,
      });
    }
    if (type === "bids") {
      const result = await importBidsExcel(buf);
      return NextResponse.json({
        ok: true,
        type,
        fileName: file.name,
        result,
        message: `登记表已合并：${result.groups} 组（中标 ${result.wonGroups} 组），新增参与 ${result.addedParticipated} / 中标 ${result.addedWon} 条，回填 ${result.backfilled} 条`,
      });
    }
    return NextResponse.json({ error: "type 必须为 daily 或 bids" }, { status: 400 });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "导入失败" }, { status: 500 });
  }
}
