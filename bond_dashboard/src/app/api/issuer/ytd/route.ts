import { DATA_DIR } from "@/lib/data-dir";
import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { internalRatingOf } from "@/lib/internal-ratings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/issuer/ytd — 返回发行人 YTD 发行分析（来自 data/issuer_ytd.json，由 scripts/build_issuer_ytd.py 生成）
// 并合并 data/cache/issuer_metrics.json 的「交易所存量债券质押比区间」与「近五年 YY 评级调整」
export async function GET() {
  try {
    const p = path.join(DATA_DIR, "issuer_ytd.json");
    if (!fs.existsSync(p)) {
      return NextResponse.json({ meta: null, monthly: [], types: [], markets: [], issuers: [], error: "缺少 data/issuer_ytd.json，请先运行 scripts/build_issuer_ytd.py" });
    }
    const data = JSON.parse(fs.readFileSync(p, "utf8")) as {
      issuers?: Array<Record<string, unknown>>;
      [k: string]: unknown;
    };

    // 合并发行人附加指标（质押比区间 + YY 评级调整）
    const metricsP = path.join(DATA_DIR, "cache", "issuer_metrics.json");
    let metricsMap: Record<string, { pledge: unknown; yyAdj: unknown }> = {};
    let metricsMeta: unknown = null;
    if (fs.existsSync(metricsP)) {
      const m = JSON.parse(fs.readFileSync(metricsP, "utf8"));
      metricsMap = m.map ?? {};
      metricsMeta = m.meta ?? null;
    }

    // 评级读时富化：issuer_ytd 内嵌的外部评级/YY 是构建日快照（同事管道重建不及时），
    // 用服务器每晚 DM 刷新的 cache/issuer_ratings_live.json 覆盖（live 值优先，缺失回退快照）
    let liveMap: Record<string, { yy?: string | null; external?: string | null }> = {};
    let ratingsMeta: { generated?: string } | null = null;
    const liveP = path.join(DATA_DIR, "cache", "issuer_ratings_live.json");
    if (fs.existsSync(liveP)) {
      try {
        const live = JSON.parse(fs.readFileSync(liveP, "utf8")) as {
          meta?: { generated?: string };
          map?: Record<string, { yy?: string | null; external?: string | null }>;
        };
        liveMap = live.map ?? {};
        ratingsMeta = live.meta ?? null;
      } catch {
        /* live 文件损坏不阻塞，退回快照值 */
      }
    }

    // 主体内评富化（信评门户数据，按发行人全称匹配）+ 附加指标（pledge/yyAdj）
    const issuers = (data.issuers ?? []).map((row) => {
      const m = metricsMap[row.issuer as string];
      const lv = liveMap[String(row.issuer ?? "").trim()];
      return {
        ...row,
        ratingExt: lv?.external || (row.ratingExt as string | null) || null,
        yy: lv?.yy || (row.yy as string | null) || null,
        internalRating: internalRatingOf(row.issuer as string) || null,
        pledge: m?.pledge ?? null, // {min,max,n} 或 null
        yyAdj: m?.yyAdj ?? null, // {up:[{from,to,date}], down:[...]} 或 null
      };
    });

    return NextResponse.json({ ...data, issuers, metricsMeta, ratingsMeta });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "读取失败", issuers: [] }, { status: 500 });
  }
}
