"use client";

import { useMemo } from "react";
import { SortableTh, useTableSort, type SortState } from "@/components/table-sort";
import { bondTypeShort } from "@/lib/format";
import { ratingRank, termSortKey, yyRank } from "@/lib/rating-sort";
import type { ExcelBond } from "@/lib/types";

/** 类型分组固定优先级（越靠前越先展示），不在表内的按首次出现顺序排最后 */
const TYPE_ORDER = [
  "SCP/CP",
  "MTN",
  "PPN",
  "公司债",
  "企业债",
  "二级资本债",
  "永续债",
  "商行债",
  "可转债",
  "可交换债",
  "ABS",
  "其他",
];

/** 分组归并：SCP 与 CP 同属短融口径，合并为一组展示（行内类型列仍各自显示） */
const TYPE_MERGE: Record<string, string> = {
  SCP: "SCP/CP",
  CP: "SCP/CP",
};

/** 各类型主题色（左侧竖条 + 徽章底色），用于视觉区分不同分组 */
const TYPE_THEME: Record<string, { bar: string; badge: string }> = {
  "SCP/CP": { bar: "#2563eb", badge: "bg-blue-100 text-blue-700" },
  MTN: { bar: "#8b5cf6", badge: "bg-violet-100 text-violet-700" },
  PPN: { bar: "#6366f1", badge: "bg-indigo-100 text-indigo-700" },
  公司债: { bar: "#f59e0b", badge: "bg-amber-100 text-amber-700" },
  企业债: { bar: "#ea580c", badge: "bg-orange-100 text-orange-700" },
  二级资本债: { bar: "#dc2626", badge: "bg-red-100 text-red-700" },
  永续债: { bar: "#e11d48", badge: "bg-rose-100 text-rose-700" },
  商行债: { bar: "#0891b2", badge: "bg-cyan-100 text-cyan-700" },
  可转债: { bar: "#16a34a", badge: "bg-green-100 text-green-700" },
  可交换债: { bar: "#65a30d", badge: "bg-lime-100 text-lime-700" },
  ABS: { bar: "#a855f7", badge: "bg-purple-100 text-purple-700" },
  其他: { bar: "#64748b", badge: "bg-slate-100 text-slate-600" },
};

function typeTheme(t: string) {
  return TYPE_THEME[t] ?? TYPE_THEME["其他"];
}

interface TypeGroup {
  type: string;
  bonds: ExcelBond[];
}

/** 按类型分组，并按固定优先级排序（未命中优先级表的按出现顺序排最后） */
function groupByType(bonds: ExcelBond[]): TypeGroup[] {
  const map = new Map<string, ExcelBond[]>();
  const order = new Map<string, number>();
  for (const b of bonds) {
    const raw = bondTypeShort(b.type);
    const t = TYPE_MERGE[raw] ?? raw;
    if (!map.has(t)) {
      map.set(t, []);
      order.set(t, order.size);
    }
    map.get(t)!.push(b);
  }
  const groups = Array.from(map.entries()).map(([type, list]) => ({ type, bonds: list }));
  const rank = (g: TypeGroup) => {
    const idx = TYPE_ORDER.indexOf(g.type);
    return idx === -1 ? TYPE_ORDER.length + (order.get(g.type) ?? 0) : idx;
  };
  groups.sort((a, b) => rank(a) - rank(b));
  return groups;
}

/** 各分组共用的可排序表头（同一排序状态在所有分组内生效） */
function GroupHeader({ sort, onSort }: { sort: SortState | null; onSort: (key: string) => void }) {
  return (
    <thead className="sticky top-9 z-10 bg-background">
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
  );
}

function BondRow({ b }: { b: ExcelBond }) {
  return (
    <tr className="border-b last:border-0 hover:bg-accent/40">
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
  );
}

/**
 * 每日 Excel《一级发行-信用债发行》发行清单表。
 * 由总览仪表盘迁出，复用为「每日一级发行」板块的发行情况表。
 * 按债券类型分组展示（SCP→CP→MTN→… 固定优先级），各组自带表头、数量与规模统计；
 * 所有列支持点击表头排序（YY/内评按评级逻辑，期限按行权前年数），排序在各分组内生效。
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
  // 先整体排序再分组：组间保持类型优先级，组内保持排序后的相对顺序
  const groups = useMemo(() => groupByType(sorted), [sorted]);

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
        {groups.map((g) => {
          const totalYi = g.bonds.reduce((s, b) => s + (Number(b.amountYi) || 0), 0);
          const theme = typeTheme(g.type);
          return (
            <div key={g.type} className="border-b last:border-0">
              {/* 组标签：彩色竖条 + 徽章 + 数量/规模，吸顶于滚动区顶部 */}
              <div
                className="sticky top-0 z-20 flex h-9 items-center gap-2.5 border-b border-l-4 bg-muted/30 px-3 backdrop-blur"
                style={{ borderLeftColor: theme.bar }}
              >
                <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-bold ${theme.badge}`}>
                  {g.type}
                </span>
                <span className="text-xs text-muted-foreground">{g.bonds.length} 只</span>
                <span className="h-1 w-1 rounded-full bg-muted-foreground/40" />
                <span className="text-xs font-medium tabular-nums text-foreground">{totalYi.toFixed(1)} 亿</span>
              </div>
              <table className="w-full text-[12px]">
                <GroupHeader sort={sort} onSort={onSort} />
                <tbody>
                  {g.bonds.map((b, i) => (
                    <BondRow key={`${b.name}-${i}`} b={b} />
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
        {!groups.length && (
          <div className="px-4 py-10 text-center text-sm text-muted-foreground">暂无债券</div>
        )}
      </div>
    </div>
  );
}
