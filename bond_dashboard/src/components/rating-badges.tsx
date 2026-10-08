// 主体资质徽章组件：统一 YY 评分 / 主体内评 / YY 调整 / 质押比 的展示样式。
// 原先各页（daily / recommended / issuer / spread）为复制粘贴的 inline span，
// 本次功能融入时抽取为共享组件。
import type { PledgeRange, YyNet } from "@/lib/issuer-metrics";

/** YY 评分徽章（violet） */
export function YYBadge({ yy }: { yy?: string | null }) {
  if (!yy) return <span className="text-muted-foreground/40">-</span>;
  return (
    <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
      {yy}
    </span>
  );
}

/** 主体内评徽章（sky） */
export function InternalRatingBadge({ rating }: { rating?: string | null }) {
  if (!rating) return <span className="text-muted-foreground/40">-</span>;
  return (
    <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
      {rating}
    </span>
  );
}

/** 近五年 YY 净调整徽章：调优N档(红) / 调差N档(绿) / 先优后差等(灰)，title 含调级明细 */
export function YYAdjBadge({
  up,
  down,
  net,
}: {
  up: { from: string; to: string; date: string }[];
  down: { from: string; to: string; date: string }[];
  net: YyNet | null | undefined;
}) {
  if (!net || net.moves <= 0) return <span className="text-muted-foreground/40">-</span>;
  const history =
    [...up.map((u) => `调高 ${u.from}→${u.to}(${u.date.slice(0, 7)})`),
     ...down.map((d) => `调低 ${d.from}→${d.to}(${d.date.slice(0, 7)})`)].join("、");
  if (net.dir === "up") {
    return (
      <span
        className="inline-flex items-center gap-0.5 rounded-md bg-red-50 px-1.5 py-0.5 text-[11px] font-semibold text-red-700 dark:bg-red-950 dark:text-red-400"
        title={`调优 ${net.from}→${net.to}（近五年净上调 ${net.steps} 档）\n明细：${history}`}
      >
        调优{net.steps}档
      </span>
    );
  }
  if (net.dir === "down") {
    return (
      <span
        className="inline-flex items-center gap-0.5 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
        title={`调差 ${net.from}→${net.to}（近五年净下调 ${net.steps} 档）\n明细：${history}`}
      >
        调差{net.steps}档
      </span>
    );
  }
  const flatLabel = net.firstDir === "up" ? "先优后差" : "先差后优";
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
      title={`维持（近五年 ${net.from}→${net.to} 净无变化，${flatLabel}）\n明细：${history}`}
    >
      {flatLabel}
    </span>
  );
}

/** 交易所质押比（标准券折算率）区间徽章，title 说明口径 */
export function PledgeBadge({ pledge }: { pledge?: PledgeRange | null }) {
  if (!pledge || pledge.n <= 0) return <span className="text-muted-foreground/40">-</span>;
  return (
    <span
      className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-sky-700 dark:bg-sky-950 dark:text-sky-400"
      title={`该发行人交易所已发债券标准券折算率（${pledge.n} 只可质押券）`}
    >
      {pledge.min === pledge.max ? pledge.min.toFixed(2) : `${pledge.min.toFixed(2)}~${pledge.max.toFixed(2)}`}
    </span>
  );
}
