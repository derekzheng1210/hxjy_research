"use client";

// 通用表头排序：useTableSort（排序状态与逻辑）+ SortableHead / SortableTh（可点击表头）。
// 交互约定：点击列头 升序 → 降序 → 恢复原始顺序；空值（null/undefined/""）恒排最后，不受升降序影响。
import { useCallback, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export type SortDir = "asc" | "desc";
export interface SortState {
  key: string;
  dir: SortDir;
}

/**
 * @param rows          原始行（不会原地修改）
 * @param valueFns      每列的取值函数：number 按数值比较，其余按中文 localeCompare；
 *                      闭包依赖外部状态（如估值表）时请用 useMemo 包裹。
 * @param initial       初始排序（默认无排序 = 保持传入顺序）
 */
export function useTableSort<T>(
  rows: T[],
  valueFns: Record<string, (row: T) => unknown>,
  initial?: SortState
) {
  const [sort, setSort] = useState<SortState | null>(initial ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const vf = valueFns[sort.key];
    if (!vf) return rows;
    const desc = sort.dir === "desc";
    return [...rows].sort((a, b) => {
      const va = vf(a);
      const vb = vf(b);
      const na = va === null || va === undefined || va === "";
      const nb = vb === null || vb === undefined || vb === "";
      if (na && nb) return 0;
      if (na) return 1;
      if (nb) return -1;
      const r =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb), "zh");
      return desc ? -r : r;
    });
  }, [rows, sort, valueFns]);

  const onSort = useCallback((key: string) => {
    setSort((s) => {
      if (s && s.key === key) {
        if (s.dir === "asc") return { key, dir: "desc" };
        return null; // 第三次点击恢复原始顺序
      }
      return { key, dir: "asc" };
    });
  }, []);

  return { sorted, sort, onSort };
}

function SortButton({
  label,
  active,
  dir,
  onSort,
}: {
  label: React.ReactNode;
  active: boolean;
  dir?: SortDir;
  onSort: () => void;
}) {
  const Icon = !active ? ArrowUpDown : dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={onSort}
      title="点击排序：升序 → 降序 → 原始顺序"
      className="inline-flex cursor-pointer items-center gap-1 whitespace-nowrap text-inherit transition-colors hover:text-foreground"
    >
      {label}
      <Icon className={cn("h-3 w-3 shrink-0", active ? "text-primary" : "opacity-40")} />
    </button>
  );
}

/** shadcn/ui Table 表头（可点击排序） */
export function SortableHead({
  sortKey,
  sort,
  onSort,
  className,
  children,
}: {
  sortKey: string;
  sort: SortState | null;
  onSort: (key: string) => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <TableHead className={className}>
      <SortButton label={children} active={sort?.key === sortKey} dir={sort?.dir} onSort={() => onSort(sortKey)} />
    </TableHead>
  );
}

/** 原生 <th> 表头（可点击排序），用于手写 <table> */
export function SortableTh({
  sortKey,
  sort,
  onSort,
  className,
  children,
}: {
  sortKey: string;
  sort: SortState | null;
  onSort: (key: string) => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <th className={className}>
      <SortButton label={children} active={sort?.key === sortKey} dir={sort?.dir} onSort={() => onSort(sortKey)} />
    </th>
  );
}
