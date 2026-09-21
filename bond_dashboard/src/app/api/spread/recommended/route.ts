import { DATA_DIR } from "@/lib/data-dir";
import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { cleanOwnCode, fetchSeriesMap, seriesCacheMeta, statOf, type SeriesMode } from "@/lib/postlist";
import { internalRatingOf } from "@/lib/internal-ratings";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** 名称归一：① 只去括号符号保留内容（Excel「...02(BC)」↔ DM「...02BC」）
 *  ② 连同括号内容一起去掉（Excel「...GN001A(碳中和债)」↔ DM「...GN001A」） */
const nKeep = (s: string) => s.replace(/[（()）\s]/g, "");
const nDrop = (s: string) => s.replace(/[（(][^）)]*[）)]/g, "").replace(/\s+/g, "");

interface RecRow {
  date: string;
  name: string;
  code?: string | null;
  term?: string | null;
  coupon?: number | null;
  plan?: number | null;
  forecast?: number | null;
  bond_type?: string | null;
  issuer?: string | null; // 发行人全称（主体内评匹配用）
}

interface BidRow {
  type?: string;
  bondName?: string;
  securityId?: string;
  coupon?: number;
}

function readJson<T>(rel: string, fallback: T): T {
  try {
    const p = path.join(DATA_DIR, rel);
    if (!fs.existsSync(p)) return fallback;
    return JSON.parse(fs.readFileSync(p, "utf8")) as T;
  } catch {
    return fallback;
  }
}

// GET /api/spread/recommended[?refresh=1 | ?mode=cached]
// 全部推荐个券：(最新估值 - 票面) * 100 bp 利差 + 发行以来估值分位数 + 参与/中标角标
// 取数模式（2026-09-11 用户要求：不再每次打开都强制刷新）：
//   ?mode=cached  页面日常打开 —— 只读本地缓存，不重复请求 DM
//   ?refresh=1    手动「强制刷新估值」/ 每日 09:30 计划任务
//   缺省          缓存不新鲜才增量拉取
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const refresh = sp.get("refresh") === "1";
  const modeParam = sp.get("mode");
  const mode: SeriesMode = refresh ? "refresh" : modeParam === "cached" ? "cached" : "auto";
  try {
    const rec = readJson<RecRow[]>("recommended.json", []);
    const bids = readJson<BidRow[]>("store/bids.json", []);
    const cal = readJson<{ days: Record<string, { bonds: { name: string; coupon?: number | null }[] }> }>(
      "excel_calendar.json",
      { days: {} }
    );

    // 票面兜底：bids → excel calendar
    const bidCoupon = new Map<string, number>();
    const participated = new Set<string>();
    const won = new Set<string>();
    for (const b of bids) {
      const nm = String(b.bondName ?? "").trim();
      if (!nm) continue;
      if (b.coupon !== null && b.coupon !== undefined && !bidCoupon.has(nm)) bidCoupon.set(nm, Number(b.coupon));
      if (b.type === "participated") participated.add(nm);
      if (b.type === "won") won.add(nm);
    }
    const calCoupon = new Map<string, number>();
    for (const day of Object.values(cal.days)) {
      for (const b of day.bonds ?? []) {
        if (b.coupon !== null && b.coupon !== undefined && !calCoupon.has(b.name)) calCoupon.set(b.name, Number(b.coupon));
      }
    }

    // 代码兜底：推荐清单「债券代码」列为空时，用 DM 简称反查 security_id
    // （scripts/build_name_code.py 由 DM 一级发行历史生成，已剔除取消发行的 DY 代码）
    const nameCode = readJson<Record<string, string>>("store/name_code.json", {});
    const lookupCode = (name: string): string | null =>
      nameCode[name] ?? nameCode[nKeep(name)] ?? nameCode[nDrop(name)] ?? null;

    const items = rec
      .filter((r) => {
        const t = String(r.bond_type ?? "");
        const n = String(r.name ?? "");
        if (t.includes("PPN") || t.includes("定向") || n.includes("PPN")) return false;
        return true;
      })
      .map((r) => {
        const coupon = r.coupon ?? bidCoupon.get(r.name) ?? calCoupon.get(r.name) ?? null;
        return {
          name: r.name,
          recDate: r.date,
          term: r.term ?? null,
          plan: r.plan ?? null,
          coupon,
          forecast: typeof r.forecast === "number" ? r.forecast : null,
          code: cleanOwnCode(r.code) ?? cleanOwnCode(lookupCode(r.name)),
          start: r.date,
          participated: participated.has(r.name),
          won: won.has(r.name),
          // 主体内评（信评门户数据，按发行人全称匹配）
          internalRating: internalRatingOf(r.issuer) || null,
        };
      });

    const series = await fetchSeriesMap(
      items.filter((i) => i.code).map((i) => ({ code: i.code as string, start: i.start })),
      { mode }
    );
    const cacheMeta = seriesCacheMeta(items.map((i) => i.code));

    const bonds = items.map((i) => {
      const st = statOf(series.get(i.code ?? "") ?? []);
      return {
        name: i.name,
        recDate: i.recDate,
        term: i.term,
        plan: i.plan,
        coupon: i.coupon,
        forecast: i.forecast,
        devBp: i.coupon !== null && i.forecast !== null ? (i.coupon - i.forecast) * 100 : null, // 预测偏差=票面−预测（正=票面高于预测/预测偏保守）
        participated: i.participated,
        won: i.won,
        latest: st.latest,
        latestDate: st.latestDate,
        percentile: st.percentile,
        obs: st.obs,
        spreadBp: st.latest !== null && i.coupon !== null ? (st.latest - i.coupon) * 100 : null,
        internalRating: i.internalRating,
      };
    });

    // 一级信用自测表：全部推荐个券中「新债预测 ≤ 实际票面」的个券（不论参与与否），
    // 检验票面预测把握度（偏差 bp）与择券盈利能力（上市后估值 vs 票面）
    const selfTest = bonds
      .filter((b) => b.forecast !== null && b.coupon !== null && (b.forecast as number) <= (b.coupon as number))
      .map((b) => ({
        ...b,
        pnlBp: b.spreadBp !== null ? -(b.spreadBp as number) : null, // 上市后盈亏=（票面−估值）×100，正=浮盈
      }))
      .sort((a, b) => (b.devBp as number) - (a.devBp as number));

    const ranked = bonds
      .filter((b) => b.spreadBp !== null && b.spreadBp !== undefined)
      .sort((a, b) => (b.spreadBp as number) - (a.spreadBp as number));
    const top10 = ranked.slice(0, 10); // 利差最高（估值高出票面最多）
    const bottom10 = ranked.slice(-10).reverse(); // 利差最低（估值低于票面最多）

    // 错失机会榜：推荐了、上市后估值低于票面（若中标上市即有浮盈）、但未参与的个券
    const missed = ranked
      .filter((b) => !b.participated && (b.spreadBp as number) < 0)
      .map((b) => ({ ...b, gainBp: -(b.spreadBp as number) })) // gainBp = 票面 − 估值（正数=错失的收益率）
      .sort((a, b) => b.gainBp - a.gainBp);

    // 躲过的亏损：推荐了、未参与、且上市后估值高于票面（若中标将亏损）
    const avoided = ranked
      .filter((b) => !b.participated && (b.spreadBp as number) > 0)
      .map((b) => ({ ...b, gainBp: b.spreadBp as number })) // gainBp = 估值 − 票面（正数=躲过的亏损）
      .sort((a, b) => b.gainBp - a.gainBp);

    // 自测统计：预测偏差 / 上市后盈亏（含中标子集）
    const stValued = selfTest.filter((b) => b.pnlBp !== null);
    const stWon = stValued.filter((b) => b.won);
    return NextResponse.json({
      bonds,
      top10,
      bottom10,
      missed,
      avoided,
      selfTest,
      meta: {
        total: bonds.length,
        valued: ranked.length,
        noCoupon: bonds.filter((b) => b.coupon === null).length,
        noVal: bonds.filter((b) => b.obs === 0).length,
        missedCount: missed.length,
        missedPlanYi: missed.reduce((s, b) => s + (b.plan ?? 0), 0),
        avoidedCount: avoided.length,
        avoidedPlanYi: avoided.reduce((s, b) => s + (b.plan ?? 0), 0),
        selfTestCount: selfTest.length,
        selfTestWon: stWon.length,
        selfTestWonWin: stWon.filter((b) => (b.pnlBp as number) > 0).length,
        selfTestWin: stValued.filter((b) => (b.pnlBp as number) > 0).length,
        selfTestLoss: stValued.filter((b) => (b.pnlBp as number) <= 0).length,
        selfTestAvgDev: selfTest.length ? selfTest.reduce((s, b) => s + (b.devBp as number), 0) / selfTest.length : 0,
        selfTestAvgPnl: stValued.length ? stValued.reduce((s, b) => s + (b.pnlBp as number), 0) / stValued.length : 0,
        latestDate: bonds.reduce<string | null>(
          (m, b) => (b.latestDate && (!m || b.latestDate > m) ? b.latestDate : m),
          null
        ),
        mode,
        cacheLatestDate: cacheMeta.maxLatestDate,
        cacheUpdatedAt: cacheMeta.lastUpdated,
        generated: new Date().toISOString(),
      },
    });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "加载失败" }, { status: 500 });
  }
}
