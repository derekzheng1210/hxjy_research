"use client";

import { useMemo } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SortableHead, useTableSort } from "@/components/table-sort";
import { termSortKey, yyRank } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";
import type { DmBond } from "@/lib/types";
import { bondTypeShort, fmtDateShort, termBucket } from "@/lib/format";

export interface BondColumn {
  key: string;
  header: string;
  className?: string;
  cell?: (b: DmBond) => React.ReactNode;
  /** 表头排序取值（不配置则该列不可排序） */
  sortValue?: (b: DmBond) => unknown;
}

const defaultCell = (key: string) => {
  const DefaultCell = (b: DmBond) => {
    const v = b[key];
    if (v === null || v === undefined || v === "") return <span className="text-muted-foreground/60">-</span>;
    return String(v);
  };
  DefaultCell.displayName = "DefaultCell";
  return DefaultCell;
};

export function bondColumns(showForecast = true, recommendSet?: Set<string> | null): BondColumn[] {
  const cols: BondColumn[] = [
    {
      key: "sec_short_name",
      header: "债券简称",
      className: "font-medium min-w-[120px]",
      sortValue: (b) => b.sec_short_name,
      cell: (b) => (
        <div>
          <div className="font-medium">
            {recommendSet?.has(b.sec_short_name ?? "") && (
              <span className="mr-1 text-amber-500" title="推荐个券">★</span>
            )}
            {b.sec_short_name || "-"}
          </div>
          {b.security_id && <div className="text-[10px] text-muted-foreground">{b.security_id}</div>}
        </div>
      ),
    },
    {
      key: "bond_type_desc",
      header: "类型",
      className: "min-w-[80px]",
      sortValue: (b) => b.bond_type_desc,
      cell: (b) => {
        const short = bondTypeShort(b.bond_type_desc);
        return (
          <span className="inline-flex rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-medium">
            {short}
          </span>
        );
      },
    },
    {
      key: "issuer_full_name",
      header: "发行人",
      className: "max-w-[160px]",
      sortValue: (b) => b.issuer_full_name,
      cell: (b) => <span className="line-clamp-1">{b.issuer_full_name || "-"}</span>,
    },
    {
      key: "issuer_yy",
      header: "YY评分",
      className: "text-center",
      sortValue: (b) => yyRank(b.issuer_yy),
      cell: (b) =>
        b.issuer_yy ? (
          <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
            {b.issuer_yy}
          </span>
        ) : (
          <span className="text-muted-foreground/40">-</span>
        ),
    },
    {
      key: "bond_issue_tenor",
      header: "期限",
      className: "text-center",
      sortValue: (b) => termSortKey(b.bond_issue_tenor),
      cell: (b) => {
        const t = b.bond_issue_tenor ? String(b.bond_issue_tenor) : null;
        const bucket = termBucket(t);
        const isLong = bucket === ">=10Y";
        return (
          <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-semibold", isLong ? "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-400" : "bg-muted")}>
            {t || "-"}
          </span>
        );
      },
    },
    {
      key: "plan_issue_amount",
      header: "计划发行(亿)",
      className: "text-right tabular-nums",
      sortValue: (b) => b.plan_issue_amount,
      cell: (b) => (b.plan_issue_amount ? (b.plan_issue_amount / 10000).toFixed(1) : <span className="text-muted-foreground/60">-</span>),
    },
    {
      key: "issue_price_forecast",
      header: "新债预测",
      className: "text-right tabular-nums",
      sortValue: (b) => b.issue_price_forecast,
      cell: (b) => (b.issue_price_forecast ? b.issue_price_forecast.toFixed(2) + "%" : <span className="text-muted-foreground/60">-</span>),
    },
    {
      key: "similar_bond_short_name",
      header: "相似二级券",
      className: "max-w-[140px]",
      sortValue: (b) => b.similar_bond_short_name,
      cell: (b) => (
        <div>
          <div className="line-clamp-1 text-[12px]">{b.similar_bond_short_name || <span className="text-muted-foreground/60">-</span>}</div>
          {b.similar_bond_code && <div className="text-[10px] text-muted-foreground">{b.similar_bond_code}</div>}
        </div>
      ),
    },
    {
      key: "similar_bond_remaining_tenor",
      header: "相似二级剩余期限",
      className: "text-center tabular-nums text-[12px]",
      sortValue: (b) => termSortKey(b.similar_bond_remaining_tenor),
      cell: (b) => b.similar_bond_remaining_tenor || <span className="text-muted-foreground/60">-</span>,
    },
    {
      key: "similar_bond_cb_valuation",
      header: "相似二级估值",
      className: "text-right tabular-nums",
      sortValue: (b) => b.similar_bond_cb_valuation,
      cell: (b) => (b.similar_bond_cb_valuation ? b.similar_bond_cb_valuation.toFixed(2) + "%" : <span className="text-muted-foreground/60">-</span>),
    },
    {
      key: "similar_bond_bid_ofr",
      header: "相似二级bid/ofr",
      className: "text-center tabular-nums text-[12px]",
      sortValue: (b) => b.similar_bond_bid_price,
      cell: (b) => {
        const bid = b.similar_bond_bid_price;
        const ofr = b.similar_bond_ofr_price;
        if (bid === null || bid === undefined || ofr === null || ofr === undefined) {
          return <span className="text-muted-foreground/60">-</span>;
        }
        return <span className="tabular-nums">{bid.toFixed(4)}/{ofr.toFixed(4)}</span>;
      },
    },
    {
      key: "spread",
      header: "利差(bp)",
      className: "text-right tabular-nums",
      sortValue: (b) =>
        b.issue_price_forecast != null && b.similar_bond_cb_valuation != null
          ? (b.issue_price_forecast - b.similar_bond_cb_valuation) * 100
          : null,
      cell: (b) => {
        const f = b.issue_price_forecast;
        const s = b.similar_bond_cb_valuation;
        if (f === null || f === undefined || s === null || s === undefined) return <span className="text-muted-foreground/60">-</span>;
        const bp = (f - s) * 100;
        return (
          <span className={cn("font-semibold", bp <= -5 ? "text-emerald-600 dark:text-emerald-400" : bp >= 5 ? "text-rose-600 dark:text-rose-400" : "")}>
            {bp >= 0 ? "+" : ""}{bp.toFixed(1)}
          </span>
        );
      },
    },
    {
      key: "subscribe_date",
      header: "截标",
      className: "text-center tabular-nums",
      sortValue: (b) => b.subscribe_date,
      cell: (b) => fmtDateShort(String(b.subscribe_date ?? "")),
    },
    {
      key: "pay_date",
      header: "缴款日",
      className: "text-center tabular-nums",
      sortValue: (b) => b.pay_date,
      cell: (b) => fmtDateShort(String(b.pay_date ?? "")),
    },
    {
      key: "issue_status_desc",
      header: "状态",
      className: "text-center",
      sortValue: (b) => b.issue_status_desc,
      cell: (b) => {
        const s = b.issue_status_desc;
        const cls =
          s === "已经上市" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
          : s === "发行完成" ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-400"
          : s === "发行中" ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400"
          : "bg-muted";
        return <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-medium", cls)}>{s || "-"}</span>;
      },
    },
  ];

  if (showForecast) {
    // 在预测后插入票面列
    cols.splice(6, 0, {
      key: "issue_yield",
      header: "票面",
      className: "text-right tabular-nums font-semibold",
      sortValue: (b) => b.issue_yield,
      cell: (b) => (b.issue_yield ? b.issue_yield.toFixed(2) + "%" : <span className="text-muted-foreground/60">-</span>),
    });
  }
  return cols;
}

export function BondTable({
  bonds,
  columns,
  emptyText = "暂无数据",
  recommendSet,
}: {
  bonds: DmBond[];
  columns?: BondColumn[];
  emptyText?: string;
  recommendSet?: Set<string> | null;
}) {
  const cols = useMemo(() => columns ?? bondColumns(undefined, recommendSet), [columns, recommendSet]);
  const valueFns = useMemo(() => {
    const fns: Record<string, (b: DmBond) => unknown> = {};
    for (const c of cols) {
      if (c.sortValue) fns[c.key] = c.sortValue;
    }
    return fns;
  }, [cols]);
  const { sorted, sort, onSort } = useTableSort(bonds, valueFns);

  if (!bonds.length) {
    return <p className="py-10 text-center text-sm text-muted-foreground">{emptyText}</p>;
  }
  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/50">
            {cols.map((c) =>
              c.sortValue ? (
                <SortableHead key={c.key} sortKey={c.key} sort={sort} onSort={onSort} className={cn("whitespace-nowrap text-xs", c.className)}>
                  {c.header}
                </SortableHead>
              ) : (
                <TableHead key={c.key} className={cn("whitespace-nowrap text-xs", c.className)}>
                  {c.header}
                </TableHead>
              )
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((b, i) => (
            <TableRow key={b.security_id || b.sec_short_name || i} className="hover:bg-accent/50">
              {cols.map((c) => (
                <TableCell key={c.key} className={cn("py-2 text-[13px]", c.className)}>
                  {c.cell ? c.cell(b) : defaultCell(c.key)(b)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
