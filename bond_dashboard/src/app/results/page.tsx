"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, ClipboardList, CircleDollarSign, Layers, GitCompareArrows, FilterX } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { LoadingState, ErrorState, EmptyState } from "@/components/status-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { SortableHead, useTableSort } from "@/components/table-sort";
import { ratingRank, termSortKey, yyRank } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";
import { cleanReason } from "@/lib/credit";
import { wanToYi, prevWorkday, fmtDateShort, couponSpreadBp, bondTypeShort } from "@/lib/format";
import type { DmBond } from "@/lib/types";

type ResultRow = DmBond & { _sp: number | null };

export default function ResultsPage() {
  const [date, setDate] = useState(prevWorkday());
  const [bonds, setBonds] = useState<ResultRow[] | null>(null);
  const [clean, setClean] = useState(true); // 信用债口径：剔永续/私募/转债/可交换（PPN 已全站剔除）
  const [error, setError] = useState("");

  const load = useCallback(async (d: string) => {
    setError("");
    setBonds(null);
    try {
      const res = await apiFetch(`/api/dm/primary?start=${d}&end=${d}&category=1&rating=1`);
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || "加载失败");
      }
      const j = await res.json();
      const rows: ResultRow[] = (j.list ?? []).map((b: DmBond) => ({
        ...b,
        _sp: couponSpreadBp(b.issue_yield, b.issue_price_forecast),
      }));
      // 过滤：有票面利率的为已定价结果
      const priced = rows.filter((b) => b.issue_yield !== null && b.issue_yield !== undefined);
      // 按票面-预测利差升序（超发在前）
      priced.sort((a, b) => (a._sp ?? 0) - (b._sp ?? 0));
      setBonds(priced);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    (async () => load(date))();
  }, [date, load]);

  // 剔除统计（信用债口径）
  const cleanStats = useMemo(() => {
    if (!bonds) return null;
    const reasons: Record<string, number> = {};
    for (const b of bonds) {
      const r = cleanReason(b);
      if (r) reasons[r] = (reasons[r] ?? 0) + 1;
    }
    return reasons;
  }, [bonds]);

  // 已定价 + 可选信用债口径清洗
  const dayBonds = useMemo(() => {
    if (!bonds) return [];
    if (!clean) return bonds;
    return bonds.filter((b) => !cleanReason(b));
  }, [bonds, clean]);

  const stats = useMemo(() => {
    if (!bonds) return null;
    const totalPlan = dayBonds.reduce((s, b) => s + (b.plan_issue_amount || 0), 0);
    const sps = dayBonds.map((b) => b._sp).filter((v): v is number => v !== null && v !== undefined);
    const avgSp = sps.length ? sps.reduce((a, b) => a + b, 0) / sps.length : null;
    const over = sps.filter((s) => s < 0).length;
    const fly = sps.filter((s) => s > 0).length;
    const mults = dayBonds.map((b) => b.compliant_subscription_mult).filter((v): v is number => v !== null && v !== undefined && v > 0);
    const avgMult = mults.length ? mults.reduce((a, b) => a + b, 0) / mults.length : null;
    const margins = dayBonds.map((b) => b.marginal_mult).filter((v): v is number => v !== null && v !== undefined && v > 0);
    const avgMargin = margins.length ? margins.reduce((a, b) => a + b, 0) / margins.length : null;
    return { total: dayBonds.length, raw: bonds.length, totalPlan, avgSp, over, fly, avgMult, avgMargin };
  }, [bonds, dayBonds]);

  const excludedTotal = cleanStats ? Object.values(cleanStats).reduce((a, b) => a + b, 0) : 0;

  // 表头排序取值（YY/内评按评级逻辑，期限按行权前年数）
  const valueFns = useMemo(
    () => ({
      sec_short_name: (b: ResultRow) => b.sec_short_name,
      bond_type_desc: (b: ResultRow) => b.bond_type_desc,
      bond_issue_tenor: (b: ResultRow) => termSortKey(b.bond_issue_tenor),
      plan_issue_amount: (b: ResultRow) => b.plan_issue_amount,
      issue_yield: (b: ResultRow) => b.issue_yield,
      issue_price_forecast: (b: ResultRow) => b.issue_price_forecast,
      _sp: (b: ResultRow) => b._sp,
      issuer_yy: (b: ResultRow) => yyRank(b.issuer_yy),
      internalRating: (b: ResultRow) => ratingRank(b.internalRating),
      compliant_subscription_mult: (b: ResultRow) => b.compliant_subscription_mult,
      marginal_mult: (b: ResultRow) => b.marginal_mult,
      province_name: (b: ResultRow) => b.province_name,
      pay_date: (b: ResultRow) => b.pay_date,
    }),
    []
  );
  const { sorted, sort, onSort } = useTableSort(dayBonds, valueFns);

  return (
    <>
      <PageHeader title="历史发行情况" description="按截标日查询历史一级发行结果：票面、利差与认购倍数">
        <div className="flex items-center gap-2">
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-9 w-auto tabular-nums" />
          <Button size="icon" variant="ghost" className="h-9 w-9" onClick={() => load(date)} aria-label="刷新">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </PageHeader>

      {error && <ErrorState message={error} onRetry={() => load(date)} />}
      {!bonds && !error && <LoadingState label={`正在从 DM 拉取 ${fmtDateShort(date)} 发行结果…`} />}

      {bonds && stats && (
        <>
          {/* 口径控制：簿记日对齐 + 信用债清洗 */}
          <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border bg-background px-4 py-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <Switch checked={clean} onCheckedChange={setClean} />
              <span className="font-medium">信用债口径</span>
              <span className="text-xs text-muted-foreground">剔永续 / 私募 / 转债 / 可交换（PPN 已全站剔除）</span>
            </label>
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <FilterX className="h-3.5 w-3.5" />
              <span>{fmtDateShort(date)} 簿记已定价 {stats.raw} 只</span>
              {cleanStats && excludedTotal > 0 && (
                <span className="flex flex-wrap gap-1">
                  {Object.entries(cleanStats).map(([k, v]) => (
                    <span key={k} className="rounded bg-rose-50 px-1.5 py-0.5 text-rose-600 dark:bg-rose-950/50 dark:text-rose-400">
                      剔 {k} {v}
                    </span>
                  ))}
                  <span className="rounded bg-muted px-1.5 py-0.5">保留 {dayBonds.length}</span>
                </span>
              )}
              {cleanStats && excludedTotal === 0 && <span>无剔除</span>}
            </div>
          </div>

          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard label="已定价只数" value={stats.total} icon={ClipboardList} accent="blue" />
            <StatCard label="发行规模" value={`${wanToYi(stats.totalPlan)} 亿`} icon={CircleDollarSign} accent="green" />
            <StatCard
              label="票面-预测均值"
              value={stats.avgSp !== null ? `${stats.avgSp >= 0 ? "+" : ""}${stats.avgSp.toFixed(1)} bp` : "-"}
              icon={GitCompareArrows}
              accent={stats.avgSp !== null && stats.avgSp < 0 ? "green" : "red"}
              sub={`超发 ${stats.over} 只 · 发飞 ${stats.fly} 只`}
            />
            <StatCard label="全场倍数均值" value={stats.avgMult ? stats.avgMult.toFixed(2) + "×" : "-"} icon={Layers} accent="violet" />
            <StatCard label="边际倍数均值" value={stats.avgMargin ? stats.avgMargin.toFixed(2) + "×" : "-"} icon={Layers} accent="amber" />
          </div>

          {!dayBonds.length ? (
            <EmptyState title={`${fmtDateShort(date)} 无已定价发行结果`} description="可能是非工作日、无发行，或清洗后无符合条件的信用债，请切换日期查看。" />
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    <SortableHead sortKey="sec_short_name" sort={sort} onSort={onSort} className="min-w-[130px]">债券简称</SortableHead>
                    <SortableHead sortKey="bond_type_desc" sort={sort} onSort={onSort}>类型</SortableHead>
                    <SortableHead sortKey="bond_issue_tenor" sort={sort} onSort={onSort} className="text-center">期限</SortableHead>
                    <SortableHead sortKey="plan_issue_amount" sort={sort} onSort={onSort} className="text-right">规模(亿)</SortableHead>
                    <SortableHead sortKey="issue_yield" sort={sort} onSort={onSort} className="text-right">票面%</SortableHead>
                    <SortableHead sortKey="issue_price_forecast" sort={sort} onSort={onSort} className="text-right">新债预测</SortableHead>
                    <SortableHead sortKey="_sp" sort={sort} onSort={onSort} className="text-right">票面-预测(bp)</SortableHead>
                    <SortableHead sortKey="issuer_yy" sort={sort} onSort={onSort} className="text-center">YY评分</SortableHead>
                    <SortableHead sortKey="internalRating" sort={sort} onSort={onSort} className="text-center">主体内评</SortableHead>
                    <SortableHead sortKey="compliant_subscription_mult" sort={sort} onSort={onSort} className="text-right">全场</SortableHead>
                    <SortableHead sortKey="marginal_mult" sort={sort} onSort={onSort} className="text-right">边际</SortableHead>
                    <SortableHead sortKey="province_name" sort={sort} onSort={onSort} className="text-center">区域</SortableHead>
                    <SortableHead sortKey="pay_date" sort={sort} onSort={onSort} className="text-center">缴款日</SortableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sorted.map((b, i) => (
                    <TableRow key={b.security_id || b.sec_short_name || i}>
                      <TableCell className="py-2 font-medium">{b.sec_short_name || "-"}</TableCell>
                      <TableCell className="py-2 text-[12px]">{bondTypeShort(b.bond_type_desc)}</TableCell>
                      <TableCell className="py-2 text-center text-[13px]">{b.bond_issue_tenor || "-"}</TableCell>
                      <TableCell className="py-2 text-right tabular-nums">{b.plan_issue_amount ? (b.plan_issue_amount / 10000).toFixed(1) : "-"}</TableCell>
                      <TableCell className="py-2 text-right font-semibold tabular-nums">{b.issue_yield ? b.issue_yield.toFixed(2) : "-"}</TableCell>
                      <TableCell className="py-2 text-right tabular-nums">{b.issue_price_forecast ? b.issue_price_forecast.toFixed(2) : "-"}</TableCell>
                      <TableCell className="py-2 text-right tabular-nums">
                        {b._sp !== null && b._sp !== undefined ? (
                          <span className={cn("font-semibold", b._sp < 0 ? "text-emerald-600 dark:text-emerald-400" : b._sp > 0 ? "text-rose-600 dark:text-rose-400" : "")}>
                            {b._sp >= 0 ? "+" : ""}{b._sp.toFixed(1)}
                          </span>
                        ) : "-"}
                      </TableCell>
                      <TableCell className="py-2 text-center text-[12px]">
                        {b.issuer_yy ? (
                          <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
                            {b.issuer_yy}
                          </span>
                        ) : (
                          "-"
                        )}
                      </TableCell>
                      <TableCell className="py-2 text-center text-[12px]">
                        {b.internalRating ? (
                          <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
                            {b.internalRating}
                          </span>
                        ) : (
                          "-"
                        )}
                      </TableCell>
                      <TableCell className="py-2 text-right tabular-nums">{b.compliant_subscription_mult ? b.compliant_subscription_mult.toFixed(2) : "-"}</TableCell>
                      <TableCell className="py-2 text-right tabular-nums">{b.marginal_mult ? b.marginal_mult.toFixed(2) : "-"}</TableCell>
                      <TableCell className="py-2 text-center text-[12px]">{b.province_name || "-"}</TableCell>
                      <TableCell className="py-2 text-center text-[13px] tabular-nums">{b.pay_date ? fmtDateShort(String(b.pay_date)) : "-"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}
    </>
  );
}
