"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, CalendarPlus, CalendarRange, ChevronLeft, ChevronRight, Layers, X, TrendingUp } from "lucide-react";
import { StatCard } from "@/components/stat-card";
import { SortableTh, useTableSort } from "@/components/table-sort";
import { termSortKey } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";
import {
  CouponCurveChart,
  colorForRecency,
  type BondDetail,
  type CurveBucket,
  type CurveSeries,
} from "@/components/coupon-curve-chart";

const MAX_DATES = 5;
const START_LABEL = "2026-01-01";

export interface CurveMeta {
  range?: string;
  yyScope?: string;
  yySource?: string;
  clean?: string;
  bucket?: string;
  weight?: string;
  generated?: string;
  bondCount?: number;
  amountYi?: number;
  couponAvg?: number | null;
  skippedGt?: number;
  skippedNoYY?: number;
}

interface DateSnapBucket {
  term: string;
  termY: number | null;
  count: number;
  amountYi: number;
  coupon: number | null;
  bonds?: BondDetail[];
}

export interface DateSnap {
  count: number;
  amountYi: number;
  couponAvg: number | null;
  buckets: DateSnapBucket[];
}

export interface DateDataset {
  dates: string[];
  series: Record<string, DateSnap>;
}

function fmtYi(v: number | undefined | null, digits = 1): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "-";
  return v.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function snapToBuckets(snap: DateSnap | undefined): CurveBucket[] {
  const all: CurveBucket[] = [];
  for (const b of snap?.buckets ?? []) {
    all.push({
      term: b.term,
      termY: b.termY,
      count: b.count,
      amountYi: b.amountYi,
      coupon: b.coupon,
      couponMin: null,
      couponMax: null,
      bonds: b.bonds ?? [],
    });
  }
  return all;
}

export function YieldCurvePanel({
  meta,
  baseBuckets,
  dataset,
}: {
  meta?: CurveMeta | null;
  baseBuckets?: CurveBucket[];
  dataset?: DateDataset | null;
}) {
  const dates = useMemo(() => dataset?.dates ?? [], [dataset]);
  const latest = dates.length ? dates[dates.length - 1] : null;

  // 初始选中：URL ?d=日期1,日期2…（可分享对比视图）；缺省取最新交易日
  const [selected, setSelected] = useState<string[]>([]);
  useEffect(() => {
    if (!latest) return;
    let init: string[] = [];
    try {
      const p = new URLSearchParams(window.location.search);
      const ds = (p.get("d") || "")
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s && dates.includes(s));
      init = ds;
    } catch {
      init = [];
    }
    if (!init.length) init = [latest];
    // 去重 + 排序（旧→新），超出 5 个上限时保留「最近 5 个」（贴近当前实际的口径）
    init = Array.from(new Set(init))
      .filter((d) => dates.includes(d))
      .sort()
      .slice(-MAX_DATES);
    (async () => setSelected(init.length ? init : [latest]))();
  }, [latest]); // eslint-disable-line react-hooks/exhaustive-deps

  const asc = useMemo(() => Array.from(new Set(selected)).sort(), [selected]);
  const colorOf = useCallback(
    (d: string) => colorForRecency(asc.length - 1 - asc.indexOf(d)),
    [asc]
  );

  const series: CurveSeries[] = useMemo(
    () =>
      asc.map((d) => ({
        date: d,
        color: colorOf(d),
        data: snapToBuckets(dataset?.series[d]),
      })),
    [asc, dataset, colorOf]
  );

  const primary = asc.length ? asc[asc.length - 1] : latest;
  const primarySnap = primary ? dataset?.series[primary] : undefined;
  const isSingleLatest = asc.length === 1 && latest !== null && asc[0] === latest;

  const addDate = (d: string) => {
    if (!d || selected.includes(d) || selected.length >= MAX_DATES) return;
    setSelected((prev) => Array.from(new Set([...prev, d])).sort());
  };
  const removeDate = (d: string) => {
    if (!latest) return;
    const rest = selected.filter((x) => x !== d);
    setSelected(rest.length ? rest : [latest]);
  };
  const toggleDate = (d: string) => {
    if (selected.includes(d)) removeDate(d);
    else addDate(d);
  };

  const rangeStart = START_LABEL;
  const rangeEnd = primary ?? rangeStart;

  // KPI 基准：默认单日视图 = 2026 年以来累计曲线(meta)；叠加对比/单日历史 = 最新选中日的「当日发行」快照
  const kpiCount = isSingleLatest
    ? (meta?.bondCount ?? primarySnap?.count ?? 0)
    : (primarySnap?.count ?? 0);
  const kpiAmt = isSingleLatest ? (meta?.amountYi ?? primarySnap?.amountYi) : primarySnap?.amountYi;
  const kpiCoupon = isSingleLatest
    ? (meta?.couponAvg ?? primarySnap?.couponAvg)
    : primarySnap?.couponAvg;

  return (
    <>
      {/* 四张 KPI 卡（默认=累计曲线；叠加/单日历史对比=最新选中日的当日发行快照） */}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="统计区间"
          value={isSingleLatest ? "2026年以来" : "当日发行对比"}
          icon={CalendarRange}
          accent="blue"
          sub={
            isSingleLatest
              ? `${rangeStart} ~ ${rangeEnd}（累计，簿记日）`
              : `${asc.length} 个簿记日 · 最新 ${rangeEnd}`
          }
        />
        <StatCard
          label="样本只数"
          value={`${kpiCount.toLocaleString("zh-CN")} 只`}
          icon={BarChart3}
          accent="green"
          sub={
            isSingleLatest
              ? "YY 1-5 档（含子档）累计"
              : `${asc.length} 个交易日 · 每列=当日发行`
          }
        />
        <StatCard
          label="发行金额合计"
          value={`${fmtYi(kpiAmt)} 亿`}
          icon={Layers}
          accent="amber"
          sub="金额=实际发行额（DM，市场真实发行）"
        />
        <StatCard
          label="加权票面（全档）"
          value={kpiCoupon != null ? `${kpiCoupon.toFixed(3)}%` : "-"}
          icon={TrendingUp}
          accent="violet"
          sub="Σ(票面×发行金额)/Σ发行金额"
        />
      </div>

      {/* 日期选择 */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">对比日期（簿记日）：</span>
        {asc.map((d) => (
          <span
            key={d}
            className="inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1 text-xs font-medium"
            style={{ borderColor: `${colorOf(d)}55` }}
          >
            <span className="h-2 w-2 rounded-full" style={{ background: colorOf(d) }} />
            {d}
            <button
              type="button"
              aria-label={`移除 ${d}`}
              className="text-muted-foreground transition hover:text-foreground"
              onClick={() => removeDate(d)}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {selected.length < MAX_DATES && dates.length > 1 && (
          <MonthDatePicker dates={dates} selected={selected} onToggle={toggleDate} />
        )}
        {selected.length >= MAX_DATES && (
          <span className="text-xs text-muted-foreground">已达上限（最多 5 个交易日）</span>
        )}
      </div>

      {/* 曲线叠加图 */}
      <div className="rounded-xl border p-4">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold">
            {isSingleLatest
              ? "信用债一级发行 票面-期限曲线（2026 年以来累计）"
              : "信用债一级发行 票面-期限曲线（当日发行对比）"}
          </p>
        </div>
        <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1">
          {asc
            .slice()
            .reverse()
            .map((d) => {
              const s = dataset?.series[d];
              return (
                <span key={d} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className="h-2 w-2 rounded-full" style={{ background: colorOf(d) }} />
                  <span className="font-semibold text-foreground/80">{d}</span>
                  <span>
                    {s ? `${s.count.toLocaleString("zh-CN")} 只 · ${fmtYi(s.amountYi)} 亿` : "-"}
                    {s?.couponAvg != null ? ` · 均 ${s.couponAvg.toFixed(3)}%` : ""}
                  </span>
                </span>
              );
            })}
        </div>
        {series.length ? <CouponCurveChart series={series} /> : <p className="py-12 text-center text-sm text-muted-foreground">请选择至少一个对比日期。</p>}
      </div>

      {/* 明细表 */}
      <div className="mt-4 overflow-x-auto rounded-xl border">
        {isSingleLatest && baseBuckets?.length ? (
          <SingleDateTable meta={meta} buckets={baseBuckets} />
        ) : (
          <MatrixTable asc={asc} dataset={dataset} colorOf={colorOf} />
        )}
      </div>
    </>
  );
}

const WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

function MonthDatePicker({
  dates,
  selected,
  onToggle,
}: {
  dates: string[];
  selected: string[];
  onToggle: (d: string) => void;
}) {
  const latest = dates[dates.length - 1];
  const startYm = dates[0].slice(0, 7);
  const endYm = latest.slice(0, 7);
  const [open, setOpen] = useState(false);
  const [viewYm, setViewYm] = useState(endYm);

  // 支持 ?cal=1 直链默认展开日历（便于分享/演示）
  useEffect(() => {
    (async () => {
      try {
        if (new URLSearchParams(window.location.search).get("cal") === "1") setOpen(true);
      } catch {
        /* ignore */
      }
    })();
  }, []);

  const daySet = useMemo(
    () => new Set(dates.filter((d) => d.startsWith(viewYm))),
    [dates, viewYm]
  );
  const selSet = useMemo(() => new Set(selected), [selected]);

  const [y, m] = [Number(viewYm.slice(0, 4)), Number(viewYm.slice(5, 7)) - 1];
  const dim = new Date(y, m + 1, 0).getDate();
  const lead = (new Date(y, m, 1).getDay() + 6) % 7; // 周一开头
  const cells: (string | null)[] = [
    ...Array<string | null>(lead).fill(null),
    ...Array.from({ length: dim }, (_, i) => `${viewYm}-${String(i + 1).padStart(2, "0")}`),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  const shift = (dir: -1 | 1) => {
    setViewYm((v) => {
      const yy = Number(v.slice(0, 4));
      const mm = Number(v.slice(5, 7));
      const d = new Date(yy, mm - 1 + dir, 1);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    });
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <span className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition",
          open
            ? "border-foreground/30 bg-accent text-foreground"
            : "border-dashed text-muted-foreground hover:border-foreground/30 hover:text-foreground"
        )}
      >
        <CalendarPlus size={12} />
        添加对比日期
      </button>

      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden />
          <div
            role="dialog"
            aria-label="选择对比簿记日"
            className="absolute left-0 top-full z-50 mt-2 w-[19rem] rounded-xl border bg-background p-3 shadow-xl"
          >
            {/* 头部：月份 + 翻月 */}
            <div className="mb-2 flex items-center justify-between">
              <button
                type="button"
                disabled={!canPrevYm(viewYm, startYm)}
                aria-label="上一月"
                onClick={() => shift(-1)}
                className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition enabled:hover:bg-accent enabled:hover:text-foreground disabled:opacity-30"
              >
                <ChevronLeft size={14} />
              </button>
              <p className="text-xs font-semibold tabular-nums">
                {y}年{m + 1}月
                <span className="ml-1.5 font-normal text-muted-foreground">
                  {daySet.size} 个簿记日
                </span>
              </p>
              <button
                type="button"
                disabled={!canNextYm(viewYm, endYm)}
                aria-label="下一月"
                onClick={() => shift(1)}
                className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition enabled:hover:bg-accent enabled:hover:text-foreground disabled:opacity-30"
              >
                <ChevronRight size={14} />
              </button>
            </div>

            {/* 星期表头 */}
            <div className="mb-1 grid grid-cols-7 gap-1">
              {WEEK_LABELS.map((w) => (
                <div
                  key={w}
                  className={cn(
                    "py-0.5 text-center text-[10px] text-muted-foreground",
                    (w === "六" || w === "日") && "opacity-70"
                  )}
                >
                  {w}
                </div>
              ))}
            </div>

            {/* 日网格 */}
            <div className="grid grid-cols-7 gap-1">
              {cells.map((cell, i) => {
                if (!cell) return <div key={i} className="h-8" />;
                const has = daySet.has(cell);
                const sel = selSet.has(cell);
                return (
                  <button
                    key={cell}
                    type="button"
                    disabled={!has}
                    onClick={() => onToggle(cell)}
                    title={has ? `${cell} · 簿记日` : `${cell} · 非簿记日（无快照）`}
                    className={cn(
                      "flex h-8 items-center justify-center rounded-md text-xs tabular-nums transition",
                      has
                        ? sel
                          ? "bg-primary font-semibold text-primary-foreground"
                          : "cursor-pointer text-foreground hover:bg-accent"
                        : "cursor-default text-muted-foreground/30"
                    )}
                  >
                    {Number(cell.slice(8, 10))}
                  </button>
                );
              })}
            </div>

            <div className="mt-2 border-t pt-2 text-[10px] leading-relaxed text-muted-foreground">
              实心格=当前对比中（再次点击移除）；灰格=非簿记日无快照；最多叠加 {MAX_DATES} 个交易日。
            </div>
          </div>
        </>
      )}
    </span>
  );
}

function canPrevYm(ym: string, startYm: string): boolean {
  return ym > startYm;
}
function canNextYm(ym: string, endYm: string): boolean {
  return ym < endYm;
}

function SingleDateTable({
  meta,
  buckets,
}: {
  meta?: CurveMeta | null;
  buckets: CurveBucket[];
}) {
  const filled = useMemo(() => buckets.filter((b) => b.count > 0), [buckets]);
  const couponAvg = meta?.couponAvg ?? null;
  const valueFns = useMemo(
    () => ({
      term: (b: CurveBucket) => termSortKey(b.term),
      count: (b: CurveBucket) => b.count,
      amountYi: (b: CurveBucket) => b.amountYi,
      coupon: (b: CurveBucket) => b.coupon,
      termY: (b: CurveBucket) => b.termY,
      couponMin: (b: CurveBucket) => b.couponMin,
    }),
    []
  );
  const { sorted, sort, onSort } = useTableSort(filled, valueFns);
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
          <SortableTh sortKey="term" sort={sort} onSort={onSort} className="px-4 py-2.5">期限档</SortableTh>
          <SortableTh sortKey="count" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right tabular-nums">只数</SortableTh>
          <SortableTh sortKey="amountYi" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right tabular-nums">发行金额(亿)</SortableTh>
          <SortableTh sortKey="coupon" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right tabular-nums">加权票面(%)</SortableTh>
          <SortableTh sortKey="termY" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right tabular-nums">加权行权前期限(年)</SortableTh>
          <SortableTh sortKey="couponMin" sort={sort} onSort={onSort} className="px-4 py-2.5 text-right tabular-nums">票面区间(%)</SortableTh>
        </tr>
      </thead>
      <tbody>
        {sorted.map((b) => (
          <tr key={b.term} className="border-b last:border-0">
            <td className="px-4 py-2.5 font-medium">{b.term}</td>
            <td className="px-4 py-2.5 text-right tabular-nums">{b.count}</td>
            <td className="px-4 py-2.5 text-right tabular-nums">{fmtYi(b.amountYi, 1)}</td>
            <td className="px-4 py-2.5 text-right font-semibold tabular-nums">
              {b.coupon !== null ? b.coupon.toFixed(3) : "-"}
            </td>
            <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">
              {b.termY !== null ? b.termY.toFixed(2) : "-"}
            </td>
            <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">
              {b.couponMin !== null && b.couponMax !== null
                ? `${b.couponMin.toFixed(2)} ~ ${b.couponMax.toFixed(2)}`
                : "-"}
            </td>
          </tr>
        ))}
        <tr className="border-t bg-muted/40 font-semibold">
          <td className="px-4 py-2.5">合计</td>
          <td className="px-4 py-2.5 text-right tabular-nums">
            {(meta?.bondCount ?? 0).toLocaleString("zh-CN")}
          </td>
          <td className="px-4 py-2.5 text-right tabular-nums">{fmtYi(meta?.amountYi, 1)}</td>
          <td className="px-4 py-2.5 text-right tabular-nums">
            {couponAvg != null ? `${couponAvg.toFixed(3)}%` : "-"}
          </td>
          <td className="px-4 py-2.5" colSpan={2} />
        </tr>
      </tbody>
    </table>
  );
}

function MatrixTable({
  asc,
  dataset,
  colorOf,
}: {
  asc: string[];
  dataset?: DateDataset | null;
  colorOf: (d: string) => string;
}) {
  const terms = asc.length
    ? (dataset?.series[asc[0]]?.buckets ?? []).map((b) => b.term)
    : [];
  const valueFns = useMemo(() => ({ term: (t: string) => termSortKey(t) }), []);
  const { sorted, sort, onSort } = useTableSort(terms, valueFns);
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
          <SortableTh sortKey="term" sort={sort} onSort={onSort} className="px-4 py-2.5">期限档</SortableTh>
          {asc.map((d) => (
            <th key={d} className="px-4 py-2.5 text-right tabular-nums">
              <span className="inline-flex items-center justify-end gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ background: colorOf(d) }} />
                {d}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((term) => (
          <tr key={term} className="border-b last:border-0">
            <td className="px-4 py-2.5 font-medium">{term}</td>
            {asc.map((d) => {
              const b = dataset?.series[d]?.buckets.find((x) => x.term === term);
              return (
                <td key={d} className="px-4 py-2.5 text-right">
                  {b && b.coupon !== null ? (
                    <>
                      <div className="font-semibold tabular-nums">{b.coupon.toFixed(3)}%</div>
                      <div className="text-[10px] text-muted-foreground tabular-nums">
                        {b.count} 只 · {fmtYi(b.amountYi)} 亿
                      </div>
                    </>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </td>
              );
            })}
          </tr>
        ))}
        <tr className="border-t bg-muted/40 font-semibold">
          <td className="px-4 py-2.5">全档加权</td>
          {asc.map((d) => {
            const s = dataset?.series[d];
            return (
              <td key={d} className="px-4 py-2.5 text-right">
                {s ? (
                  <>
                    <div className="tabular-nums">
                      {s.couponAvg != null ? `${s.couponAvg.toFixed(3)}%` : "-"}
                    </div>
                    <div className="text-[10px] font-normal text-muted-foreground tabular-nums">
                      {s.count.toLocaleString("zh-CN")} 只 · {fmtYi(s.amountYi)} 亿
                    </div>
                  </>
                ) : (
                  "-"
                )}
              </td>
            );
          })}
        </tr>
      </tbody>
    </table>
  );
}
