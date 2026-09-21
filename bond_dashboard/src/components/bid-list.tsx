"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Trash2, RefreshCw } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { LoadingState, ErrorState, EmptyState } from "@/components/status-state";
import { SortableHead, useTableSort } from "@/components/table-sort";
import { ratingRank, termSortKey, yyRank } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";
import type { BidRecord } from "@/lib/types";

interface Valuation {
  security_id?: string | null;
  cb?: number | null; // 中债估值（含权取行权）
  cs?: number | null; // 中证估值（含权取行权）
  market?: string | null;
}

/** 期限 → 行权前年限："5+5Y"→5、"10Y"→10、"3+2"→3；无法解析返回 null */
function termToYears(term?: string | null): number | null {
  if (!term) return null;
  const m = /^(\d+(?:\.\d+)?)/.exec(String(term).trim());
  return m ? Number(m[1]) : null;
}

/**
 * 修正久期与理论价：按年付息、行权/到期期末还本、面值 100。
 * yPct 为收益率(%)（对应中债/中证估值，含权取行权）。
 */
function modDuration(T: number, coupon: number, yPct: number): { dur: number; price: number } {
  const n = Math.max(1, Math.round(T));
  const r = 1 + yPct / 100;
  let price = 0;
  let mac = 0;
  for (let t = 1; t < n; t++) {
    const df = Math.pow(r, -t);
    price += coupon * df;
    mac += t * coupon * df;
  }
  const dfN = Math.pow(r, -n);
  price += (100 + coupon) * dfN;
  mac += n * (100 + coupon) * dfN;
  mac /= price;
  return { dur: mac / r, price };
}

/** DV01（万元）= 名义(亿元) × 修正久期 × (理论价/100) × 0.0001 → amount × dur × price / 100 */
function dv01Wan(amount: number, dur: number, price: number): number {
  return (amount * dur * price) / 100;
}

/** 该行对应估值（银行间取中债、交易所取中证；含权为行权口径） */
function rowValOf(b: BidRecord, vals: Record<string, Valuation>): number | null {
  const v = vals[b.bondName];
  if (!v) return null;
  const cb = v.cb;
  if (cb !== null && cb !== undefined && !Number.isNaN(cb)) return cb;
  const cs = v.cs;
  if (cs !== null && cs !== undefined && !Number.isNaN(cs)) return cs;
  return null;
}

/** 估值-票面(bp) =（对应估值 − 票面）× 100 */
function valMinusCouponBpOf(b: BidRecord, vals: Record<string, Valuation>): number | null {
  if (b.coupon === null || b.coupon === undefined || Number.isNaN(b.coupon)) return null;
  const val = rowValOf(b, vals);
  if (val === null) return null;
  return Math.round((val - b.coupon) * 1000) / 10;
}

/** 中标组合汇总口径（权重 = 中标量 amount，亿元） */
interface WonStats {
  count: number;
  amountSum: number;
  couponCount: number;
  termCount: number;
  valCount: number;
  wCoupon: number | null; // 加权票面
  wTerm: number | null; // 加权期限（行权前）
  wVal: number | null; // 加权估值（对应市场）
  dv01: number; // 组合 DV01（万元）
}

export function BidList({ type }: { type: "participated" | "won" }) {
  const [bids, setBids] = useState<BidRecord[] | null>(null);
  const [vals, setVals] = useState<Record<string, Valuation>>({});
  const [valDate, setValDate] = useState("");
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const isWon = type === "won";

  const load = useCallback(async () => {
    setError("");
    try {
      const [bidsRes, valRes] = await Promise.all([
        apiFetch(`/api/bids?type=${type}`),
        apiFetch("/api/valuations"),
      ]);
      if (!bidsRes.ok) throw new Error("加载失败");
      const j = await bidsRes.json();
      setBids(j.list ?? []);
      if (valRes.ok) {
        const v = await valRes.json();
        setVals(v.bonds ?? {});
        setValDate(v.date ?? "");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, [type]);

  useEffect(() => {
    (async () => load())();
  }, [load]);

  // 表头排序取值（估值列依赖 vals，用 useMemo 跟随其变化）
  const valueFns = useMemo(
    () => ({
      bondName: (b: BidRecord) => b.bondName,
      term: (b: BidRecord) => termSortKey(b.term),
      amount: (b: BidRecord) => b.amount,
      coupon: (b: BidRecord) => b.coupon,
      yy: (b: BidRecord) => yyRank(b.yy),
      internalRating: (b: BidRecord) => ratingRank(b.internalRating),
      valBp: (b: BidRecord) => valMinusCouponBpOf(b, vals),
      cb: (b: BidRecord) => vals[b.bondName]?.cb,
      cs: (b: BidRecord) => vals[b.bondName]?.cs,
      bidDate: (b: BidRecord) => b.bidDate,
      payDate: (b: BidRecord) => b.payDate,
    }),
    [vals]
  );
  const { sorted, sort, onSort } = useTableSort(bids ?? [], valueFns);

  async function remove(id: string) {
    if (!confirm("确认删除该条记录？")) return;
    setDeleting(id);
    try {
      await apiFetch(`/api/bids?id=${id}`, { method: "DELETE" });
      await load();
    } finally {
      setDeleting(null);
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!bids) return <LoadingState />;
  if (!bids.length) {
    return (
      <EmptyState
        title={type === "participated" ? "暂无参与记录" : "暂无中标记录"}
        description="通过右上角「录入」按钮记录你的投标情况。"
      />
    );
  }

  const totalAmount = bids.reduce((s, b) => s + (b.amount || 0), 0);

  function valCell(bondName: string, kind: "cb" | "cs") {
    const v = vals[bondName]?.[kind];
    if (v === null || v === undefined || Number.isNaN(v)) {
      return <span className="text-muted-foreground/40">-</span>;
    }
    return <span className="tabular-nums font-medium">{v.toFixed(2)}%</span>;
  }

  /** 中标组合加权汇总（仅展示层使用） */
  function wonStats(rows: BidRecord[]): WonStats | null {
    if (!isWon) return null;
    const usable = rows.filter((b) => b.amount && b.amount > 0);
    const s: WonStats = {
      count: usable.length,
      amountSum: 0,
      couponCount: 0,
      termCount: 0,
      valCount: 0,
      wCoupon: null,
      wTerm: null,
      wVal: null,
      dv01: 0,
    };
    let sumC = 0;
    let sumT = 0;
    let sumV = 0;
    for (const b of usable) {
      const a = b.amount;
      s.amountSum += a;
      const c = b.coupon;
      if (c !== null && c !== undefined && !Number.isNaN(c)) {
        s.couponCount++;
        sumC += a * c;
      }
      const T = termToYears(b.term);
      if (T !== null) {
        s.termCount++;
        sumT += a * T;
      }
      const y = rowValOf(b, vals);
      if (y !== null) {
        s.valCount++;
        sumV += a * y;
        const hasCoupon = c !== null && c !== undefined && !Number.isNaN(c);
        if (T !== null && hasCoupon) {
          const dm = modDuration(T, c as number, y);
          s.dv01 += dv01Wan(a, dm.dur, dm.price);
        }
      }
    }
    if (s.couponCount > 0) s.wCoupon = sumC / s.amountSum;
    if (s.termCount > 0) s.wTerm = sumT / s.amountSum;
    if (s.valCount > 0) s.wVal = sumV / s.amountSum;
    return s;
  }

  const stats = wonStats(bids);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        {isWon && stats ? (
          <p className="text-sm text-muted-foreground">
            中标组合 <span className="font-semibold text-foreground">{stats.count}</span> 笔，合计{" "}
            <span className="font-semibold text-foreground">{stats.amountSum.toFixed(1)}</span> 亿元
            {valDate && <span className="ml-2 text-[11px]">估值日 {valDate.slice(5).replace("-", "/")}（DM）</span>}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            共 <span className="font-semibold text-foreground">{bids.length}</span> 笔，合计{" "}
            <span className="font-semibold text-foreground">{totalAmount.toFixed(1)}</span> 亿元
            {valDate && (
              <span className="ml-2 text-[11px]">估值日 {valDate.slice(5).replace("-", "/")}（DM）</span>
            )}
          </p>
        )}
        <Button size="sm" variant="ghost" onClick={load}>
          <RefreshCw className="mr-1 h-3.5 w-3.5" /> 刷新
        </Button>
      </div>

      {isWon && stats && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="rounded-xl border border-sky-200/80 bg-sky-50/60 p-3.5 dark:border-sky-900 dark:bg-sky-950/25">
            <p className="flex items-center gap-1.5 text-xs font-medium text-sky-700 dark:text-sky-300">
              <span className="inline-block h-2 w-2 rounded-full bg-sky-500" />中标总量
            </p>
            <p className="mt-1.5 text-2xl font-bold tabular-nums leading-none text-sky-700 dark:text-sky-300">
              {stats.amountSum.toFixed(1)}
              <span className="ml-1 text-sm font-medium text-muted-foreground">亿元</span>
            </p>
            <p className="mt-1.5 text-[11px] text-muted-foreground">{stats.count} 笔</p>
          </div>
          <div className="rounded-xl border border-amber-200/80 bg-amber-50/60 p-3.5 dark:border-amber-900 dark:bg-amber-950/25">
            <p className="flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-300">
              <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />加权票面
            </p>
            <p className="mt-1.5 text-2xl font-bold tabular-nums leading-none text-amber-700 dark:text-amber-300">
              {stats.wCoupon !== null ? stats.wCoupon.toFixed(3) : "-"}
              <span className="ml-1 text-sm font-medium text-muted-foreground">%</span>
            </p>
            <p className="mt-1.5 text-[11px] text-muted-foreground">权重 = 中标量</p>
          </div>
          <div className="rounded-xl border border-violet-200/80 bg-violet-50/60 p-3.5 dark:border-violet-900 dark:bg-violet-950/25">
            <p className="flex items-center gap-1.5 text-xs font-medium text-violet-700 dark:text-violet-300">
              <span className="inline-block h-2 w-2 rounded-full bg-violet-500" />加权期限
            </p>
            <p className="mt-1.5 text-2xl font-bold tabular-nums leading-none text-violet-700 dark:text-violet-300">
              {stats.wTerm !== null ? stats.wTerm.toFixed(2) : "-"}
              <span className="ml-1 text-sm font-medium text-muted-foreground">年</span>
            </p>
            <p className="mt-1.5 text-[11px] text-muted-foreground">行权前期限 · 权重 = 中标量</p>
          </div>
          <div className="rounded-xl border border-rose-200/80 bg-rose-50/60 p-3.5 dark:border-rose-900 dark:bg-rose-950/25">
            <p className="flex items-center gap-1.5 text-xs font-medium text-rose-700 dark:text-rose-300">
              <span className="inline-block h-2 w-2 rounded-full bg-rose-500" />DV01
            </p>
            <p className="mt-1.5 text-2xl font-bold tabular-nums leading-none text-rose-700 dark:text-rose-300">
              {stats.dv01.toFixed(1)}
              <span className="ml-1 text-sm font-medium text-muted-foreground">万元</span>
            </p>
            <p className="mt-1.5 text-[11px] text-muted-foreground">修正久期法估算</p>
          </div>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              <SortableHead sortKey="bondName" sort={sort} onSort={onSort} className="min-w-[140px] whitespace-nowrap text-xs">债券简称</SortableHead>
              <SortableHead sortKey="term" sort={sort} onSort={onSort} className="whitespace-nowrap text-center text-xs">期限</SortableHead>
              <SortableHead sortKey="amount" sort={sort} onSort={onSort} className="whitespace-nowrap text-right text-xs">金额(亿)</SortableHead>
              <SortableHead sortKey="coupon" sort={sort} onSort={onSort} className="whitespace-nowrap text-right text-xs">票面%</SortableHead>
              <SortableHead sortKey="yy" sort={sort} onSort={onSort} className="whitespace-nowrap text-center text-xs">YY评分</SortableHead>
              <SortableHead sortKey="internalRating" sort={sort} onSort={onSort} className="whitespace-nowrap text-center text-xs">主体内评</SortableHead>
              <SortableHead sortKey="valBp" sort={sort} onSort={onSort} className="whitespace-nowrap text-right text-xs">估值-票面(bp)</SortableHead>
              <SortableHead sortKey="cb" sort={sort} onSort={onSort} className="whitespace-nowrap text-right text-xs">中债估值</SortableHead>
              <SortableHead sortKey="cs" sort={sort} onSort={onSort} className="whitespace-nowrap text-right text-xs">中证估值</SortableHead>
              <SortableHead sortKey="bidDate" sort={sort} onSort={onSort} className="whitespace-nowrap text-center text-xs">投标日期</SortableHead>
              <SortableHead sortKey="payDate" sort={sort} onSort={onSort} className="whitespace-nowrap text-center text-xs">缴款日</SortableHead>
              <TableHead className="text-center w-12">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((b) => (
              <TableRow key={b.id}>
                <TableCell>
                  <div className="font-medium">{b.bondName}</div>
                  {b.issuer && <div className="text-[11px] text-muted-foreground">{b.issuer}</div>}
                </TableCell>
                <TableCell className="text-center text-[13px]">{b.term || "-"}</TableCell>
                <TableCell className="text-right tabular-nums font-semibold">{b.amount.toFixed(1)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {b.coupon ? (
                    b.coupon.toFixed(2)
                  ) : b.couponNote ? (
                    <span
                      className={cn(
                        "inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-semibold whitespace-nowrap",
                        /取消/.test(b.couponNote)
                          ? "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400"
                          : "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-400"
                      )}
                    >
                      {b.couponNote}
                    </span>
                  ) : (
                    "-"
                  )}
                </TableCell>
                <TableCell className="text-center text-[12px]">
                  {/^[1-8](\+|-)?$/.test(String(b.yy ?? "")) ? (
                    <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
                      {b.yy}
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40" title={b.yy ? `YY 原值：${b.yy}（非标准档位）` : undefined}>
                      -
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-center text-[12px]">
                  {b.internalRating ? (
                    <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
                      {b.internalRating}
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40">-</span>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {(() => {
                    const bp = valMinusCouponBpOf(b, vals);
                    if (bp === null) return <span className="text-muted-foreground/40">-</span>;
                    return (
                      <span className={cn("font-medium", bp < 0 ? "text-rose-600 dark:text-rose-400" : bp > 0 ? "text-emerald-600 dark:text-emerald-400" : "")}>
                        {bp >= 0 ? "+" : ""}{bp.toFixed(1)}
                      </span>
                    );
                  })()}
                </TableCell>
                <TableCell className="text-right text-[13px]">{valCell(b.bondName, "cb")}</TableCell>
                <TableCell className="text-right text-[13px]">{valCell(b.bondName, "cs")}</TableCell>
                <TableCell className="text-center text-[13px] tabular-nums">{b.bidDate}</TableCell>
                <TableCell className="text-center text-[13px] tabular-nums">{b.payDate || "-"}</TableCell>
                <TableCell className="text-center">
                  <Button size="icon" variant="ghost" className="h-7 w-7 text-muted-foreground hover:text-destructive" onClick={() => remove(b.id)} disabled={deleting === b.id}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
          {isWon && stats && (
            <TableFooter>
              <TableRow className="bg-muted/60">
                <TableCell>
                  <div className="font-semibold">合计 / 加权</div>
                  <div className="text-[11px] font-normal text-muted-foreground">{stats.count} 笔</div>
                </TableCell>
                <TableCell className="text-center text-muted-foreground/60">-</TableCell>
                <TableCell className="text-right tabular-nums font-bold">
                  {stats.amountSum.toFixed(1)}
                </TableCell>
                <TableCell className="text-right tabular-nums font-semibold">
                  {stats.wCoupon !== null ? stats.wCoupon.toFixed(3) : "-"}
                  {stats.couponCount < stats.count && <span className="text-destructive">*</span>}
                </TableCell>
                <TableCell className="text-center text-muted-foreground/60">-</TableCell>
                <TableCell className="text-center text-muted-foreground/60">-</TableCell>
                <TableCell className="text-right tabular-nums font-semibold">
                  {stats.wCoupon !== null && stats.wVal !== null ? (
                    <span className={cn((stats.wVal - stats.wCoupon) * 100 < 0 ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400")}>
                      {((stats.wVal - stats.wCoupon) * 100 >= 0 ? "+" : "")}
                      {((stats.wVal - stats.wCoupon) * 100).toFixed(1)}
                    </span>
                  ) : (
                    "-"
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums font-semibold" colSpan={2}>
                  {stats.wVal !== null ? `${stats.wVal.toFixed(3)}%` : "-"}
                  {stats.valCount < stats.count && <span className="text-destructive">*</span>}
                </TableCell>
                <TableCell className="text-center text-muted-foreground/60">-</TableCell>
                <TableCell className="text-center text-muted-foreground/60">-</TableCell>
                <TableCell />
              </TableRow>
            </TableFooter>
          )}
        </Table>
      </div>
    </div>
  );
}
