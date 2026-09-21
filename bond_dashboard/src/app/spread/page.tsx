"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, TrendingDown, TrendingUp, Percent, Landmark, Trophy, CircleSlash, Target } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { LoadingState, ErrorState, EmptyState } from "@/components/status-state";
import { Button } from "@/components/ui/button";
import { SortableTh, useTableSort } from "@/components/table-sort";
import { ratingRank, termSortKey } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";

// ===== 个券上市后估值行 =====
interface OwnValRow {
  name: string;
  code: string | null;
  tenor: string | null;
  matu: string | null;
  coupon: number | null;
  yy: string | null;
  start: string | null;
  latest: number | null;
  latestDate: string | null;
  percentile: number | null;
  obs: number;
  firstDate: string | null;
  spreadBp: number | null;
  internalRating?: string | null; // 主体内评（信评门户数据）
}

interface RecValRow extends OwnValRow {
  recDate: string;
  term?: string | null;
  plan: number | null;
  participated: boolean;
  won: boolean;
  gainBp?: number; // 错失收益/躲过亏损（正数）
  forecast?: number | null; // 新债预测（自测表用）
  devBp?: number | null; // 预测偏差=票面−预测（正=票面高于预测）
  pnlBp?: number | null; // 上市后盈亏=票面−估值（正=浮盈）
}

interface RecResp {
  bonds: RecValRow[];
  top10: RecValRow[];
  bottom10: RecValRow[];
  missed: RecValRow[];
  avoided: RecValRow[];
  selfTest: RecValRow[];
  meta: {
    total: number; valued: number; noCoupon: number; noVal: number;
    missedCount: number; missedPlanYi: number; avoidedCount: number; avoidedPlanYi: number;
    selfTestCount: number; selfTestWon: number; selfTestWonWin: number;
    selfTestWin: number; selfTestLoss: number; selfTestAvgDev: number; selfTestAvgPnl: number;
    latestDate: string | null;
    mode?: string;
    cacheLatestDate?: string | null;
    cacheUpdatedAt?: string | null;
  };
}

const bpFmt = (v: number | null | undefined) =>
  v === null || v === undefined ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;

/** 角标：参与 / 中标 */
function BidBadges({ p, w }: { p: boolean; w: boolean }) {
  if (!p && !w) return null;
  return (
    <span className="ml-1.5 inline-flex shrink-0 gap-1 align-middle">
      {p && (
        <span className="inline-flex items-center rounded bg-sky-100 px-1 py-px text-[10px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
          参与
        </span>
      )}
      {w && (
        <span className="inline-flex items-center rounded bg-amber-100 px-1 py-px text-[10px] font-semibold text-amber-700 dark:bg-amber-950 dark:text-amber-400">
          中标
        </span>
      )}
    </span>
  );
}

function SpreadCell({ v }: { v: number | null }) {
  if (v === null) return <span className="text-muted-foreground">-</span>;
  return (
    <span className={cn("font-semibold", v < 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400")}>
      {bpFmt(v)}
    </span>
  );
}

function PctCell({ p }: { p: number | null }) {
  if (p === null) return <span className="text-muted-foreground">-</span>;
  return (
    <span className={cn("tabular-nums", p >= 70 ? "text-rose-600 dark:text-rose-400" : p <= 30 ? "text-emerald-600 dark:text-emerald-400" : "")}>
      {p}%
    </span>
  );
}

export default function SpreadPage() {
  // ── 第 2 块：推荐个券利差榜 ──
  const [rec, setRec] = useState<RecResp | null>(null);
  const [recErr, setRecErr] = useState("");
  const [recLoading, setRecLoading] = useState(false);

  // 取数（2026-09-11 用户要求）：
  //   打开页面 = 只读本地缓存（mode=cached），不重复请求 DM；
  //   每日 09:30 由计划任务调 ?refresh=1 自动刷新一次；
  //   需要立即取最新估值时点「强制刷新估值」（refresh=1）。
  const loadRec = useCallback(async (refresh = false) => {
    setRecErr("");
    setRecLoading(true);
    try {
      const res = await apiFetch(`/api/spread/recommended${refresh ? "?refresh=1" : "?mode=cached"}`);
      let j = await res.json();
      if (!res.ok) throw new Error(j.error || "加载失败");
      // 首次使用/缓存为空时回退到常规取数（按需增量拉取一次），避免页面空白
      if (!refresh && (j?.meta?.valued ?? 0) === 0 && (j?.meta?.total ?? 0) > 0) {
        const res2 = await apiFetch("/api/spread/recommended");
        if (res2.ok) j = await res2.json();
      }
      setRec(j as RecResp);
    } catch (e) {
      setRecErr(e instanceof Error ? e.message : "加载失败");
    } finally {
      setRecLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => loadRec())();
  }, [loadRec]);

  // 一级信用自测表：表头排序取值
  const selfTestFns = useMemo(
    () => ({
      name: (b: RecValRow) => b.name,
      term: (b: RecValRow) => termSortKey(b.term),
      plan: (b: RecValRow) => b.plan,
      forecast: (b: RecValRow) => b.forecast,
      coupon: (b: RecValRow) => b.coupon,
      devBp: (b: RecValRow) => b.devBp,
      latest: (b: RecValRow) => b.latest,
      pnlBp: (b: RecValRow) => b.pnlBp,
      percentile: (b: RecValRow) => b.percentile,
      internalRating: (b: RecValRow) => ratingRank(b.internalRating),
    }),
    []
  );
  const { sorted: selfTestRows, sort: stSort, onSort: stOnSort } = useTableSort(rec?.selfTest ?? [], selfTestFns);

  return (
    <>
      <PageHeader
        title="一级-二级利差分析"
        description="个券一级发行票面 vs 上市后最新估值（利差 =（最新估值 − 票面）× 100 bp，负值=上市即浮盈），并给出发行以来估值分位数"
      />

      {/* ================= 第 2 块：推荐个券利差榜 ================= */}
      <section className="mb-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Trophy className="h-4 w-4 text-amber-500" />
            推荐个券利差榜（全部推荐券，按（最新估值 − 票面）× 100 bp 排序）
          </h2>
          <Button
            size="sm"
            variant="outline"
            className="h-8"
            disabled={recLoading}
            title="立即从 DM 拉取最新估值（页面日常打开只读缓存，不会重复请求）"
            onClick={() => loadRec(true)}
          >
            <RefreshCw className={cn("mr-1 h-3.5 w-3.5", recLoading && "animate-spin")} />
            手动刷新估值
          </Button>
        </div>
        {recErr && <ErrorState message={recErr} onRetry={() => loadRec()} />}
        {recLoading && !rec && <LoadingState label="正在读取估值缓存…" />}
        {rec && (
          rec.meta.valued === 0 ? (
            <EmptyState title="推荐个券暂无可计算的利差" description="推荐券均未上市或缺少票面，待上市后自动补齐。" />
          ) : (
            <>
              <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                <span>推荐券 {rec.meta.total} 只</span>
                <span>已上市有估值 {rec.meta.valued} 只</span>
                {rec.meta.noVal > 0 && <span>未上市/无估值 {rec.meta.noVal} 只</span>}
                {rec.meta.noCoupon > 0 && <span>缺票面 {rec.meta.noCoupon} 只</span>}
                {rec.meta.latestDate && <span>估值截至 {rec.meta.latestDate}</span>}
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <div className="overflow-x-auto rounded-xl border border-rose-200 dark:border-rose-900">
                  <p className="border-b bg-rose-50 px-4 py-2 text-[13px] font-semibold text-rose-700 dark:bg-rose-950/40 dark:text-rose-400">
                    利差最高 TOP10（估值高出票面最多 · 上市后走弱）
                  </p>
                  <RecTable rows={rec.top10} />
                </div>
                <div className="overflow-x-auto rounded-xl border border-emerald-200 dark:border-emerald-900">
                  <p className="border-b bg-emerald-50 px-4 py-2 text-[13px] font-semibold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400">
                    利差最低 TOP10（估值低于票面最多 · 上市后上涨）
                  </p>
                  <RecTable rows={rec.bottom10} />
                </div>
              </div>
            </>
          )
        )}
      </section>

      {/* ================= 第 2.5 块：未参与复盘（错失浮盈 vs 躲过亏损） ================= */}
      {rec && (!!(rec.missed?.length ?? 0) || !!(rec.avoided?.length ?? 0)) && (
        <section className="mb-6">
          <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <CircleSlash className="h-4 w-4 text-sky-500" />
            未参与复盘：错失的浮盈 vs 躲过的亏损（全部推荐券 · 未参与口径）
          </h2>

          {/* —— 错失的浮盈 —— */}
          {!!rec.missed?.length && (
            <>
              <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatCard label="错失浮盈个券" value={rec.meta.missedCount} icon={CircleSlash} accent="blue" />
                <StatCard label="合计计划规模" value={`${rec.meta.missedPlanYi.toFixed(1)} 亿`} icon={Landmark} accent="blue" />
                <StatCard
                  label="平均错失收益"
                  value={`${(rec.missed.reduce((s, b) => s + b.gainBp!, 0) / rec.missed.length).toFixed(1)} bp`}
                  icon={Percent}
                  accent="red"
                />
                <StatCard label="最大错失收益" value={`${rec.missed[0].gainBp!.toFixed(1)} bp`} icon={TrendingDown} accent="red" />
              </div>
              <div className="mb-5 overflow-x-auto rounded-xl border border-rose-200 dark:border-rose-900">
                <p className="border-b bg-rose-50 px-4 py-2 text-[13px] font-semibold text-rose-700 dark:bg-rose-950/40 dark:text-rose-400">
                  错失的浮盈：估值低于票面（按（票面 − 估值）× 100 bp 降序）
                </p>
                <SideTable rows={rec.missed} mode="gain" />
              </div>
            </>
          )}

          {/* —— 躲过的亏损 —— */}
          {!!rec.avoided?.length && (
            <>
              <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatCard label="若中标将亏个券" value={rec.meta.avoidedCount} icon={CircleSlash} accent="blue" />
                <StatCard label="合计计划规模" value={`${rec.meta.avoidedPlanYi.toFixed(1)} 亿`} icon={Landmark} accent="blue" />
                <StatCard
                  label="平均躲过亏损"
                  value={`${(rec.avoided.reduce((s, b) => s + b.gainBp!, 0) / rec.avoided.length).toFixed(1)} bp`}
                  icon={Percent}
                  accent="green"
                />
                <StatCard label="最大躲过亏损" value={`${rec.avoided[0].gainBp!.toFixed(1)} bp`} icon={TrendingUp} accent="green" />
              </div>
              <div className="overflow-x-auto rounded-xl border border-emerald-200 dark:border-emerald-900">
                <p className="border-b bg-emerald-50 px-4 py-2 text-[13px] font-semibold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400">
                  躲过的亏损：估值高于票面（按（估值 − 票面）× 100 bp 降序）
                </p>
                <SideTable rows={rec.avoided} mode="loss" />
              </div>
            </>
          )}
        </section>
      )}

      {/* ================= 第 2.6 块：一级信用自测表 ================= */}
      {rec && !!rec.selfTest?.length && (
        <section className="mb-6">
          <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <Target className="h-4 w-4 text-violet-500" />
            一级信用自测表：票面预测把握度 × 择券盈利能力
          </h2>
          <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="自测个券（预测≤票面）" value={rec.meta.selfTestCount} icon={Target} accent="blue" />
            <StatCard label="平均预测偏差" value={`${rec.meta.selfTestAvgDev.toFixed(1)} bp`} icon={Percent} accent="blue" />
            <StatCard
              label="上市后浮盈 / 亏损"
              value={`${rec.meta.selfTestWin} / ${rec.meta.selfTestLoss}`}
              icon={TrendingDown}
              accent={rec.meta.selfTestWin >= rec.meta.selfTestLoss ? "green" : "red"}
            />
            <StatCard
              label="中标且浮盈"
              value={`${rec.meta.selfTestWonWin} / ${rec.meta.selfTestWon}`}
              icon={Trophy}
              accent="amber"
            />
          </div>
              <div className="overflow-x-auto rounded-xl border border-violet-200 dark:border-violet-900">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-violet-50 text-left text-xs text-muted-foreground dark:bg-violet-950/40">
                      <SortableTh sortKey="name" sort={stSort} onSort={stOnSort} className="px-4 py-2.5">债券简称</SortableTh>
                      <SortableTh sortKey="term" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-center">期限</SortableTh>
                      <SortableTh sortKey="plan" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-right">计划(亿)</SortableTh>
                      <SortableTh sortKey="forecast" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-right">新债预测%</SortableTh>
                      <SortableTh sortKey="coupon" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-right">实际票面%</SortableTh>
                      <SortableTh sortKey="devBp" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-right">预测偏差(bp)</SortableTh>
                      <th className="px-4 py-2.5 text-center">参与/中标</th>
                      <SortableTh sortKey="latest" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-right">最新估值%</SortableTh>
                      <SortableTh sortKey="pnlBp" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-right">上市后盈亏(bp)</SortableTh>
                      <SortableTh sortKey="percentile" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-center">发行以来分位</SortableTh>
                      <SortableTh sortKey="internalRating" sort={stSort} onSort={stOnSort} className="px-4 py-2.5 text-center">主体内评</SortableTh>
                    </tr>
                  </thead>
                  <tbody>
                    {selfTestRows.map((b) => (
                  <tr key={b.name + b.recDate} className="border-b last:border-0 hover:bg-accent/40">
                    <td className="px-4 py-2 font-medium">
                      {b.name}
                      <div className="text-[11px] text-muted-foreground">{b.recDate} 推荐</div>
                    </td>
                    <td className="px-4 py-2 text-center tabular-nums text-[12px]">{b.term || "-"}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{b.plan ?? "-"}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{b.forecast !== null && b.forecast !== undefined ? b.forecast.toFixed(2) : "-"}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{b.coupon !== null ? b.coupon.toFixed(2) : "-"}</td>
                    <td className="px-4 py-2 text-right">
                      <span className="font-semibold tabular-nums text-violet-600 dark:text-violet-400">+{(b.devBp ?? 0).toFixed(1)}</span>
                    </td>
                    <td className="px-4 py-2 text-center"><BidBadges p={b.participated} w={b.won} /></td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {b.latest !== null ? b.latest.toFixed(2) : <span className="text-muted-foreground">未上市/无估值</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {b.pnlBp === null || b.pnlBp === undefined ? (
                        <span className="text-muted-foreground">-</span>
                      ) : (
                        <span className={cn("font-semibold tabular-nums", b.pnlBp > 0 ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400")}>
                          {b.pnlBp > 0 ? "+" : ""}{b.pnlBp.toFixed(1)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-center"><PctCell p={b.percentile} /></td>
                    <td className="px-4 py-2 text-center">
                      {b.internalRating ? (
                        <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">{b.internalRating}</span>
                      ) : (
                        <span className="text-muted-foreground/40">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

    </>
  );
}

/** 未参与复盘共用表格：mode=gain 错失的浮盈（绿）/ mode=loss 躲过的亏损（红） */
function SideTable({ rows, mode }: { rows: RecValRow[]; mode: "gain" | "loss" }) {
  const valueFns = useMemo(
    () => ({
      name: (b: RecValRow) => b.name,
      term: (b: RecValRow) => termSortKey(b.term),
      plan: (b: RecValRow) => b.plan,
      coupon: (b: RecValRow) => b.coupon,
      latest: (b: RecValRow) => b.latest,
      gainBp: (b: RecValRow) => b.gainBp,
      percentile: (b: RecValRow) => b.percentile,
      latestDate: (b: RecValRow) => b.latestDate,
      internalRating: (b: RecValRow) => ratingRank(b.internalRating),
    }),
    []
  );
  const { sorted, sort, onSort } = useTableSort(rows, valueFns);

  if (!rows.length) {
    return <div className="px-4 py-6 text-center text-sm text-muted-foreground">暂无数据</div>;
  }
  const max = Math.max(...rows.map((r) => r.gainBp ?? 0), 1);
  const gain = mode === "gain";
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          <SortableTh sortKey="name" sort={sort} onSort={onSort} className="px-4 py-2.5">债券简称</SortableTh>
          <SortableTh sortKey="term" sort={sort} onSort={onSort} className="px-4 py-2.5 text-center">期限</SortableTh>
          <SortableTh sortKey="plan" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right">计划(亿)</SortableTh>
          <SortableTh sortKey="coupon" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right">票面%</SortableTh>
          <SortableTh sortKey="latest" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right">最新估值%</SortableTh>
          <SortableTh sortKey="gainBp" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right">{gain ? "错失收益(bp)" : "躲过亏损(bp)"}</SortableTh>
          <SortableTh sortKey="percentile" sort={sort} onSort={onSort} className="px-4 py-2.5 text-center">发行以来分位</SortableTh>
          <SortableTh sortKey="latestDate" sort={sort} onSort={onSort} className="px-4 py-2.5 text-center">估值日</SortableTh>
          <SortableTh sortKey="internalRating" sort={sort} onSort={onSort} className="px-4 py-2.5 text-center">主体内评</SortableTh>
        </tr>
      </thead>
      <tbody>
        {sorted.map((b) => (
          <tr key={b.name + b.recDate} className="border-b last:border-0 hover:bg-accent/40">
            <td className="px-4 py-2 font-medium">
              {b.name}
              <div className="text-[11px] text-muted-foreground">{b.recDate} 推荐</div>
            </td>
            <td className="px-4 py-2 text-center tabular-nums text-[12px]">{b.term || "-"}</td>
            <td className="px-4 py-2 text-right tabular-nums">{b.plan ?? "-"}</td>
            <td className="px-4 py-2 text-right tabular-nums">{b.coupon !== null ? b.coupon.toFixed(2) : "-"}</td>
            <td className="px-4 py-2 text-right tabular-nums">{b.latest !== null ? b.latest.toFixed(2) : "-"}</td>
            <td className="px-4 py-2 text-right">
              <span className={cn("font-semibold", gain ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400")}>
                +{b.gainBp!.toFixed(1)}
              </span>
              <div className="mt-1 h-1 w-full rounded bg-muted">
                <div
                  className={cn("h-1 rounded", gain ? "bg-rose-500" : "bg-emerald-500")}
                  style={{ width: `${Math.min(100, ((b.gainBp ?? 0) / max) * 100)}%` }}
                />
              </div>
            </td>
            <td className="px-4 py-2 text-center"><PctCell p={b.percentile} /></td>
            <td className="px-4 py-2 text-center tabular-nums text-[12px] text-muted-foreground">{b.latestDate || "-"}</td>
            <td className="px-4 py-2 text-center">
              {b.internalRating ? (
                <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">{b.internalRating}</span>
              ) : (
                <span className="text-muted-foreground/40">-</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 推荐个券榜表格（TOP10 共用） */
function RecTable({ rows }: { rows: RecValRow[] }) {
  const valueFns = useMemo(
    () => ({
      name: (b: RecValRow) => b.name,
      coupon: (b: RecValRow) => b.coupon,
      latest: (b: RecValRow) => b.latest,
      spreadBp: (b: RecValRow) => b.spreadBp,
      percentile: (b: RecValRow) => b.percentile,
      internalRating: (b: RecValRow) => ratingRank(b.internalRating),
    }),
    []
  );
  const { sorted, sort, onSort } = useTableSort(rows, valueFns);

  if (!rows.length) {
    return <div className="px-4 py-6 text-center text-sm text-muted-foreground">暂无数据</div>;
  }
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.spreadBp ?? 0)), 1);
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          <SortableTh sortKey="name" sort={sort} onSort={onSort} className="px-3 py-2">债券简称</SortableTh>
          <SortableTh sortKey="coupon" sort={sort} onSort={onSort} className="px-3 py-2 text-right">票面%</SortableTh>
          <SortableTh sortKey="latest" sort={sort} onSort={onSort} className="px-3 py-2 text-right">最新估值%</SortableTh>
          <SortableTh sortKey="spreadBp" sort={sort} onSort={onSort} className="px-3 py-2 text-right">利差(bp)</SortableTh>
          <SortableTh sortKey="percentile" sort={sort} onSort={onSort} className="px-3 py-2 text-center">分位</SortableTh>
          <SortableTh sortKey="internalRating" sort={sort} onSort={onSort} className="px-3 py-2 text-center">主体内评</SortableTh>
        </tr>
      </thead>
      <tbody>
        {sorted.map((b) => (
          <tr key={b.name + b.recDate} className="border-b last:border-0 hover:bg-accent/40">
            <td className="px-3 py-2">
              <div className="flex items-center">
                <span className="truncate font-medium" title={`${b.name}（推荐日 ${b.recDate}）`}>
                  {b.name}
                </span>
                <BidBadges p={b.participated} w={b.won} />
              </div>
              <div className="text-[11px] text-muted-foreground">{b.recDate} 推荐{b.obs ? ` · 估值 ${b.latestDate ?? "-"}` : ""}</div>
            </td>
            <td className="px-3 py-2 text-right tabular-nums">{b.coupon !== null ? b.coupon.toFixed(2) : "-"}</td>
            <td className="px-3 py-2 text-right tabular-nums">{b.latest !== null ? b.latest.toFixed(2) : "-"}</td>
            <td className="px-3 py-2 text-right">
              <SpreadCell v={b.spreadBp} />
              {/* 利差幅度条 */}
              {b.spreadBp !== null && (
                <div className="mt-1 h-1 w-full rounded bg-muted">
                  <div
                    className={cn("h-1 rounded", (b.spreadBp ?? 0) < 0 ? "bg-emerald-500" : "bg-rose-500")}
                    style={{ width: `${Math.min(100, (Math.abs(b.spreadBp ?? 0) / maxAbs) * 100)}%` }}
                  />
                </div>
              )}
            </td>
            <td className="px-3 py-2 text-center"><PctCell p={b.percentile} /></td>
            <td className="px-3 py-2 text-center">
              {b.internalRating ? (
                <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">{b.internalRating}</span>
              ) : (
                <span className="text-muted-foreground/40">-</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
