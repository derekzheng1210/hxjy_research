import { DATA_DIR } from "@/lib/data-dir";
import { NextRequest, NextResponse } from "next/server";
import { statSync } from "fs";
import { readFile } from "fs/promises";
import path from "path";
import { ensureBondStats } from "@/lib/issuer-vals";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface IssuerBond {
  name: string;
  code: string;
  tenor: string | null;
  type: string;
  planYi: number;
  actYi: number;
  coupon: number | null;
  date: string;
  market: string;
  val?: {
    latest: number | null; // 最新估值（%）
    latestDate: string | null;
    pct: number | null; // 发行以来估值分位数 0-100
    obs: number; // 估值观测数
  } | null;
}

interface BondsStore {
  meta: { generated: string; range: string; issuerCount: number; bondCount: number };
  issuers: Record<string, IssuerBond[]>;
}

let cache: { mtime: number; store: BondsStore } | null = null;

async function loadStore(): Promise<BondsStore> {
  const p = path.join(DATA_DIR, "issuer_bonds.json");
  const st = statSync(p);
  if (cache && cache.mtime === st.mtimeMs) return cache.store;
  const raw = await readFile(p, "utf-8");
  cache = { mtime: st.mtimeMs, store: JSON.parse(raw) as BondsStore };
  return cache.store;
}

// GET /api/issuer/bonds?name=<发行人全称> → 该发行人今年以来个券明细（期限从长到短）
// 附带每只券的最新估值 + 发行以来分位数（复用 own_series.json 缓存；首次展开会回补缺口并落盘）
export async function GET(req: NextRequest) {
  try {
    const name = req.nextUrl.searchParams.get("name");
    if (!name) return NextResponse.json({ error: "缺少 name 参数" }, { status: 400 });
    const store = await loadStore();
    const bonds = store.issuers[name] ?? [];
    // 估值补齐失败不阻塞明细返回（估值列显示 "-"）
    const valMeta: { valuationAsOf: string | null } = { valuationAsOf: null };
    let withVal = bonds;
    try {
      const stats = await ensureBondStats(bonds.map((b) => ({ code: b.code, start: b.date })));
      withVal = bonds.map((b) => {
        const st = stats.get(b.code);
        if (!st) return { ...b, val: null };
        if (st.latestDate && (!valMeta.valuationAsOf || st.latestDate > valMeta.valuationAsOf)) {
          valMeta.valuationAsOf = st.latestDate;
        }
        return {
          ...b,
          val: { latest: st.latest, latestDate: st.latestDate, pct: st.pct, obs: st.obs },
        };
      });
    } catch {
      withVal = bonds.map((b) => ({ ...b, val: null }));
    }
    return NextResponse.json({
      name,
      count: withVal.length,
      bonds: withVal,
      valuationAsOf: valMeta.valuationAsOf,
      meta: store.meta,
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "读取发行人个券明细失败" },
      { status: 500 }
    );
  }
}
