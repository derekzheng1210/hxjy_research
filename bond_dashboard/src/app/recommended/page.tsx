"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Star, CircleDollarSign, Percent, Layers, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { LoadingState, ErrorState, EmptyState } from "@/components/status-state";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SortableHead, useTableSort } from "@/components/table-sort";
import { ratingRank, termSortKey, yyRank } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";
import { bondTypeShort } from "@/lib/format";

interface RecBond {
  date: string;
  name: string;
  code?: string | null;
  term?: string | null;
  plan?: number | null;
  actual?: number | null; // DM 最终发行规模（亿元）
  coupon?: number | null;
  coupon_raw?: string | null; // 票面列原文（回拨/取消发行标记）
  couponNote?: string | null; // 票面缺失原因标注（取消发行 / 回拨X年 / 未截标）
  sp_bp?: number | null;
  forecast?: number | null;
  sec_bond?: string | null;
  sec_val?: number | null;
  issuer?: string | null;
  yy?: string | null; // YY 评分（来自每日 Excel）
  internalRating?: string | null; // 主体内评（信评门户数据）
  region?: string | null;
  bond_type?: string | null;
  pay_date?: string | null;
}

export default function RecommendedPage() {
  const [bonds, setBonds] = useState<RecBond[] | null>(null);
  const [vals, setVals] = useState<Record<string, { cb?: number | null; cs?: number | null }>>({});
  const [error, setError] = useState("");
  const [dateFrom, setDateFrom] = useState("2026-06-15");
  const [dateTo, setDateTo] = useState("2026-08-31");
  // 默认日期跟随数据：加载后自动对齐到推荐数据的最早 ~ 最新日期（用户手动改过则不再覆盖）
  const dateTouched = useRef(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const [recRes, valRes] = await Promise.all([
        apiFetch("/api/recommended"),
        apiFetch("/api/valuations"),
      ]);
      if (!recRes.ok) throw new Error("加载失败");
      const j = await recRes.json();
      const list: RecBond[] = j.list ?? [];
      setBonds(list);
      if (!dateTouched.current && list.length) {
        const dates = list.map((b) => b.date).sort();
        setDateFrom(dates[0]);
        setDateTo(dates[dates.length - 1]);
      }
      if (valRes.ok) {
        const v = await valRes.json();
        setVals(v.bonds ?? {});
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    (async () => load())();
  }, [load]);

  const filtered = useMemo(() => {
    if (!bonds) return [];
    return bonds
      .filter((b) => b.date >= dateFrom && b.date <= dateTo)
      .sort((a, b) => b.date.localeCompare(a.date));
  }, [bonds, dateFrom, dateTo]);

  // 表头排序取值（估值列依赖 vals，用 useMemo 跟随其变化）
  const valueFns = useMemo(
    () => ({
      date: (b: RecBond) => b.date,
      name: (b: RecBond) => b.name,
      bond_type: (b: RecBond) => b.bond_type,
      term: (b: RecBond) => termSortKey(b.term),
      scale: (b: RecBond) => b.actual ?? b.plan,
      coupon: (b: RecBond) => b.coupon,
      cb: (b: RecBond) => vals[b.name]?.cb,
      cs: (b: RecBond) => vals[b.name]?.cs,
      valBp: (b: RecBond) => {
        const v = vals[b.name];
        const val = v?.cb !== null && v?.cb !== undefined ? v.cb : v?.cs;
        return b.coupon !== null && b.coupon !== undefined && val !== null && val !== undefined
          ? (val - b.coupon) * 100
          : null;
      },
      sp_bp: (b: RecBond) => b.sp_bp,
      yy: (b: RecBond) => yyRank(b.yy),
      issuer: (b: RecBond) => b.issuer,
      internalRating: (b: RecBond) => ratingRank(b.internalRating),
      pay_date: (b: RecBond) => b.pay_date,
    }),
    [vals]
  );
  const { sorted, sort, onSort } = useTableSort(filtered, valueFns);

  const stats = useMemo(() => {
    if (!filtered.length) return null;
    // 发行规模用 DM 最终发行规模（actual），回退计划发行
    const planSum = filtered.reduce((s, b) => s + ((b.actual ?? b.plan) || 0), 0);
    const priced = filtered.filter((b) => b.coupon !== null && b.coupon !== undefined);
    const sps = filtered
      .map((b) => b.sp_bp)
      .filter((v): v is number => v !== null && v !== undefined);
    const avgSp = sps.length ? sps.reduce((a, b) => a + b, 0) / sps.length : null;
    const over = sps.filter((s) => s < 0).length;
    return { total: filtered.length, planSum, priced: priced.length, avgSp, over };
  }, [filtered]);

  return (
    <>
      <PageHeader
        title="推荐个券"
        description="每日发行清单中「是否推荐=是」的个券汇总"
      >
        <div className="flex items-center gap-2">
          <Input
            type="date"
            value={dateFrom}
            onChange={(e) => {
              dateTouched.current = true;
              setDateFrom(e.target.value);
            }}
            className="h-9 w-auto tabular-nums"
          />
          <span className="text-sm text-muted-foreground">~</span>
          <Input
            type="date"
            value={dateTo}
            onChange={(e) => {
              dateTouched.current = true;
              setDateTo(e.target.value);
            }}
            className="h-9 w-auto tabular-nums"
          />
          <Button size="icon" variant="ghost" className="h-9 w-9" onClick={load} aria-label="刷新">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </PageHeader>

      {error && <ErrorState message={error} onRetry={load} />}
      {!bonds && !error && <LoadingState label="加载推荐个券…" />}

      {bonds && !bonds.length && (
        <EmptyState title="暂无推荐个券数据" description="缺少 data/recommended.json，请先运行 Excel 导入脚本。" />
      )}

      {bonds && bonds.length > 0 && stats && (
        <>
          {!filtered.length ? (
            <EmptyState title="该日期范围无推荐个券" description="请调整筛选日期。" />
          ) : (
            <>
              <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatCard label="推荐只数" value={stats.total} icon={Star} accent="amber" />
                <StatCard label="发行规模合计" value={`${stats.planSum.toFixed(1)} 亿`} icon={CircleDollarSign} accent="blue" />
                <StatCard label="已定价只数" value={stats.priced} icon={Percent} accent="green" />
                <StatCard
                  label="票面-预测均值"
                  value={stats.avgSp !== null ? `${stats.avgSp >= 0 ? "+" : ""}${stats.avgSp.toFixed(1)} bp` : "-"}
                  icon={Layers}
                  accent={stats.avgSp !== null && stats.avgSp < 0 ? "green" : "red"}
                  sub={`超发 ${stats.over} 只`}
                />
              </div>

              <div className="overflow-x-auto rounded-xl border">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/50">
                      <SortableHead sortKey="date" sort={sort} onSort={onSort} className="min-w-[90px]">个券推荐日期</SortableHead>
                      <SortableHead sortKey="name" sort={sort} onSort={onSort} className="min-w-[130px]">债券简称</SortableHead>
                      <SortableHead sortKey="bond_type" sort={sort} onSort={onSort}>类型</SortableHead>
                      <SortableHead sortKey="term" sort={sort} onSort={onSort} className="text-center">期限</SortableHead>
                      <SortableHead sortKey="scale" sort={sort} onSort={onSort} className="text-right">发行规模(亿)</SortableHead>
                      <SortableHead sortKey="coupon" sort={sort} onSort={onSort} className="text-right">票面</SortableHead>
                      <SortableHead sortKey="cb" sort={sort} onSort={onSort} className="text-right">中债估值</SortableHead>
                      <SortableHead sortKey="cs" sort={sort} onSort={onSort} className="text-right">中证估值</SortableHead>
                      <SortableHead sortKey="valBp" sort={sort} onSort={onSort} className="text-right">估值-票面(bp)</SortableHead>
                      <SortableHead sortKey="sp_bp" sort={sort} onSort={onSort} className="text-right">票面-预测(bp)</SortableHead>
                      <SortableHead sortKey="yy" sort={sort} onSort={onSort} className="text-center">YY评分</SortableHead>
                      <SortableHead sortKey="issuer" sort={sort} onSort={onSort} className="min-w-[120px]">发行主体</SortableHead>
                      <SortableHead sortKey="internalRating" sort={sort} onSort={onSort} className="text-center">主体内评</SortableHead>
                      <SortableHead sortKey="pay_date" sort={sort} onSort={onSort} className="text-center">缴款日</SortableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sorted.map((b, i) => (
                      <TableRow key={`${b.date}-${b.name}-${i}`} className="hover:bg-accent/50">
                        <TableCell className="py-2 text-[12px] tabular-nums">{b.date}</TableCell>
                        <TableCell className="py-2">
                          <div className="flex items-center gap-1 font-medium">
                            <Star className="h-3 w-3 shrink-0 fill-amber-400 text-amber-400" />
                            {b.name}
                          </div>
                          {b.code && <div className="text-[10px] text-muted-foreground">{b.code}</div>}
                        </TableCell>
                        <TableCell className="py-2 text-[12px]">{bondTypeShort(b.bond_type)}</TableCell>
                        <TableCell className="py-2 text-center text-[13px] tabular-nums">{b.term || "-"}</TableCell>
                        <TableCell className="py-2 text-right tabular-nums">
                          {(b.actual ?? b.plan) ? (b.actual ?? b.plan)?.toFixed(1) : "-"}
                        </TableCell>
                        <TableCell className="py-2 text-right">
                          {b.coupon_raw ? (
                            b.coupon_raw.includes("回拨") ? (
                              <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-700 dark:bg-amber-950 dark:text-amber-400">
                                <RefreshCw className="h-3 w-3" />
                                {b.coupon_raw}
                              </span>
                            ) : b.coupon_raw.includes("取消") ? (
                              <span className="inline-flex items-center gap-1 rounded-md bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold text-rose-700 dark:bg-rose-950 dark:text-rose-400">
                                ✕ {b.coupon_raw}
                              </span>
                            ) : (
                              <span className="text-muted-foreground/60">{b.coupon_raw}</span>
                            )
                          ) : b.coupon ? (
                            <span className="tabular-nums">{b.coupon.toFixed(2)}%</span>
                          ) : b.couponNote ? (
                            b.couponNote.includes("取消") ? (
                              <span className="inline-flex items-center gap-1 rounded-md bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold text-rose-700 dark:bg-rose-950 dark:text-rose-400">
                                ✕ {b.couponNote}
                              </span>
                            ) : b.couponNote.includes("回拨") ? (
                              <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-700 dark:bg-amber-950 dark:text-amber-400">
                                <RefreshCw className="h-3 w-3" />
                                {b.couponNote}
                              </span>
                            ) : (
                              <span className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                                {b.couponNote}
                              </span>
                            )
                          ) : (
                            <span className="text-muted-foreground/60">-</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2 text-right text-[13px] tabular-nums">
                          {vals[b.name]?.cb !== null && vals[b.name]?.cb !== undefined ? (
                            <span className="font-medium">{vals[b.name]?.cb?.toFixed(2)}%</span>
                          ) : (
                            <span className="text-muted-foreground/40">-</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2 text-right text-[13px] tabular-nums">
                          {vals[b.name]?.cs !== null && vals[b.name]?.cs !== undefined ? (
                            <span className="font-medium">{vals[b.name]?.cs?.toFixed(2)}%</span>
                          ) : (
                            <span className="text-muted-foreground/40">-</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2 text-right tabular-nums">
                          {(() => {
                            // 估值-票面(bp) = (对应估值 − 票面) × 100；银行间取中债、交易所取中证
                            const v = vals[b.name];
                            const val = v?.cb !== null && v?.cb !== undefined ? v.cb : v?.cs;
                            if (b.coupon !== null && b.coupon !== undefined && val !== null && val !== undefined) {
                              const bp = Math.round((val - b.coupon) * 1000) / 10;
                              return (
                                <span className={cn("font-semibold", bp < 0 ? "text-rose-600 dark:text-rose-400" : bp > 0 ? "text-emerald-600 dark:text-emerald-400" : "")}>
                                  {bp >= 0 ? "+" : ""}{bp.toFixed(1)}
                                </span>
                              );
                            }
                            return <span className="text-muted-foreground/60">-</span>;
                          })()}
                        </TableCell>
                        <TableCell className="py-2 text-right tabular-nums">
                          {b.sp_bp !== null && b.sp_bp !== undefined ? (
                            <span className={cn("font-semibold", b.sp_bp < 0 ? "text-emerald-600 dark:text-emerald-400" : b.sp_bp > 0 ? "text-rose-600 dark:text-rose-400" : "")}>
                              {b.sp_bp >= 0 ? "+" : ""}{b.sp_bp.toFixed(1)}
                            </span>
                          ) : (
                            <span className="text-muted-foreground/60">-</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2 text-center text-[12px]">
                          {b.yy ? (
                            <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
                              {b.yy}
                            </span>
                          ) : (
                            "-"
                          )}
                        </TableCell>
                        <TableCell className="max-w-[180px] py-2">
                          <span className="line-clamp-2 text-[12px]" title={b.issuer || ""}>{b.issuer || "-"}</span>
                        </TableCell>
                        <TableCell className="py-2 text-center text-[12px]">
                          {b.internalRating ? (
                            <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
                              {b.internalRating}
                            </span>
                          ) : (
                            <span className="text-muted-foreground/40">-</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2 text-center text-[12px] tabular-nums">{b.pay_date || "-"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
