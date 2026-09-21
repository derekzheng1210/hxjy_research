"use client";

import { useMemo } from "react";
import { SortableTh, useTableSort } from "@/components/table-sort";
import { bondTypeShort } from "@/lib/format";
import { ratingRank, termSortKey, yyRank } from "@/lib/rating-sort";
import type { ExcelBond } from "@/lib/types";

/**
 * 每日 Excel《一级发行-信用债发行》发行清单表。
 * 由总览仪表盘迁出，复用为「每日一级发行」板块的发行情况表。
 * 所有列支持点击表头排序（YY/内评按评级逻辑，期限按行权前年数）。
 */
export function IssueListTable({
  bonds,
  title,
  file,
  maxHeight = 620,
}: {
  bonds: ExcelBond[];
  title?: string;
  file?: string | null;
  maxHeight?: number;
}) {
  const valueFns = useMemo(
    () => ({
      name: (b: ExcelBond) => b.name,
      issuer: (b: ExcelBond) => b.issuer,
      type: (b: ExcelBond) => b.type,
      tenor: (b: ExcelBond) => termSortKey(b.tenor),
      amountYi: (b: ExcelBond) => b.amountYi,
      forecast: (b: ExcelBond) => b.forecast,
      coupon: (b: ExcelBond) => b.coupon,
      yy: (b: ExcelBond) => yyRank(b.yy),
      internalRating: (b: ExcelBond) => ratingRank(b.internalRating),
      payDate: (b: ExcelBond) => b.payDate,
    }),
    []
  );
  const { sorted, sort, onSort } = useTableSort(bonds, valueFns);

  return (
    <div className="rounded-xl border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <p className="text-sm font-semibold">
          {title ?? "发行清单"}
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            {file ? `${file} · ` : ""}
            {bonds.length} 只
          </span>
        </p>
        <span className="text-[11px] text-muted-foreground">黄色 ★ = 清单标记推荐 · 点击表头排序</span>
      </div>
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full text-[12px]">
          <thead className="sticky top-0 z-10 bg-background">
            <tr className="border-b text-left text-muted-foreground">
              <SortableTh sortKey="name" sort={sort} onSort={onSort} className="px-3 py-2 font-medium">债券简称</SortableTh>
              <SortableTh sortKey="issuer" sort={sort} onSort={onSort} className="min-w-[120px] px-2 py-2 font-medium">发行人</SortableTh>
              <SortableTh sortKey="type" sort={sort} onSort={onSort} className="px-2 py-2 font-medium">类型</SortableTh>
              <SortableTh sortKey="tenor" sort={sort} onSort={onSort} className="px-2 py-2 text-right font-medium">期限</SortableTh>
              <SortableTh sortKey="amountYi" sort={sort} onSort={onSort} className="px-2 py-2 text-right font-medium">规模(亿)</SortableTh>
              <SortableTh sortKey="forecast" sort={sort} onSort={onSort} className="px-2 py-2 text-right font-medium">新债预测%</SortableTh>
              <SortableTh sortKey="coupon" sort={sort} onSort={onSort} className="px-2 py-2 text-right font-medium">票面%</SortableTh>
              <SortableTh sortKey="yy" sort={sort} onSort={onSort} className="px-2 py-2 text-right font-medium">YY</SortableTh>
              <SortableTh sortKey="internalRating" sort={sort} onSort={onSort} className="px-2 py-2 text-center font-medium">主体内评</SortableTh>
              <SortableTh sortKey="payDate" sort={sort} onSort={onSort} className="px-3 py-2 text-center font-medium">缴款日</SortableTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((b, i) => (
              <tr key={`${b.name}-${i}`} className="border-b last:border-0 hover:bg-accent/40">
                <td className="px-3 py-1.5">
                  {b.recommended && <span className="mr-1 text-amber-500">★</span>}
                  <span className={b.recommended ? "font-semibold" : ""}>{b.name}</span>
                </td>
                <td className="max-w-[200px] px-2 py-1.5 text-muted-foreground">
                  <span className="line-clamp-1">{b.issuer || "-"}</span>
                </td>
                <td className="px-2 py-1.5 text-muted-foreground">{b.type ? bondTypeShort(b.type) : "-"}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{b.tenor ?? "-"}</td>
                <td className="px-2 py-1.5 text-right font-medium tabular-nums">{b.amountYi}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {b.forecast === null || b.forecast === undefined ? (
                    <span className="text-muted-foreground/60">-</span>
                  ) : (
                    b.forecast.toFixed(2)
                  )}
                </td>
                <td className="px-2 py-1.5 text-right font-semibold tabular-nums">
                  {b.coupon === null || b.coupon === undefined ? (
                    <span className="font-normal text-muted-foreground/60">-</span>
                  ) : (
                    b.coupon.toFixed(2)
                  )}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">{b.yy ?? "-"}</td>
                <td className="px-2 py-1.5 text-center">
                  {b.internalRating ? (
                    <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
                      {b.internalRating}
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40">-</span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-center tabular-nums">{b.payDate ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
