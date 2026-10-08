"use client";

// 单日发行「期限结构散点」：由一级偏离统计迁入（2026-09-24），只展示所选日期。
// 分档统计/类型分解/票面直方图留在一级偏离统计（区间口径），此处不重复。
// X 轴按业务阅读习惯非线性且随当日发行期限自适应：短端占画布 62%、长端压缩占 38%，刻度仍标真实年数。
import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { bondTypeShort } from "@/lib/format";

/**
 * 期限文本 → 行权前年数（首段口径："270D"→0.74、"5+N"→5、"3+2Y"→3）。
 * 注意不能用 rating-sort 的 termSortKey：那是复合排序键（主键×10000+全额期限，"5"→50005），不是年数。
 */
function termYears(v: string | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const m = /^(\d+(?:\.\d+)?)\s*([YDM年月天日]?)/.exec(String(v).trim().toUpperCase());
  if (!m) return null;
  const n = Number(m[1]);
  const u = m[2];
  if (u === "D" || u === "天" || u === "日") return n / 365;
  if (u === "M" || u === "月") return n / 12;
  return n;
}

export interface StructBond {
  name: string;
  tenor: string | null;
  coupon: number | null;
  amountWan: number | null;
  typeDesc: string | null;
}

const MAX_YEAR = 30;
// 横轴右端从档位里选"≥当日最长期限"的最小值，短端发行日不把画布浪费到 30Y 压缩区
const AXIS_LADDER = [1, 2, 3, 5, 7, 10, 15, 20, 30];
const SPLIT_POS = 0.62;

/** 非线性期限轴：[0,split]Y → [0,0.62]，(split,axisMax]Y → [0.62,1]；axisMax=split 时线性 */
function buildTermAxis(maxTerm: number) {
  const axisMax = Math.max(1, AXIS_LADDER.find((v) => v >= maxTerm) ?? MAX_YEAR);
  const split = axisMax > 5 ? 5 : axisMax > 1 ? axisMax / 2 : axisMax;
  const xMap = (t: number) => {
    const v = Math.min(t, axisMax);
    if (v <= split) return (v / split) * SPLIT_POS;
    return SPLIT_POS + ((v - split) / (axisMax - split)) * (1 - SPLIT_POS);
  };
  const tickYears = (axisMax <= 1 ? [0.25, 0.5, 1] : [0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30]).filter(
    (y) => y <= axisMax,
  );
  const ticks = tickYears.map((y) => Number(xMap(y).toFixed(6)));
  const tickLabels = new Map(ticks.map((pos, i) => [pos, `${tickYears[i]}Y`]));
  return { axisMax, xMap, ticks, tickLabels };
}

const TYPE_COLORS: Record<string, string> = {
  SCP: "#2563eb", CP: "#2563eb", "SCP/CP": "#2563eb",
  MTN: "#8b5cf6", PPN: "#0d9488", 公司债: "#f59e0b",
  企业债: "#ea580c", 二级资本债: "#dc2626", 永续债: "#e11d48", 商行债: "#0891b2",
  可转债: "#16a34a", 可交换债: "#65a30d", ABS: "#a855f7", 其他: "#64748b",
};

interface Point {
  x: number;
  y: number;
  z: number;
  name: string;
  term: number;
  type: string;
}

function ScatterTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  if (p.term === undefined) return null; // 趋势线锚点不弹卡片
  return (
    <div className="rounded-md border bg-background px-3 py-2 text-xs shadow-sm">
      <p className="font-semibold">{p.name}</p>
      <p className="text-muted-foreground">{p.type} · 期限 {p.term.toFixed(2)}Y</p>
      <p className="tabular-nums">票面 {p.y.toFixed(4)}% · 规模 {p.z.toFixed(1)}亿</p>
    </div>
  );
}

/** 单日期限结构散点（ScatterChart 标准容器；X 轴随当日期限自适应非线性、点大小=规模 6~16px、附分桶中位票面趋势线） */
export function StructurePanel({ bonds, date }: { bonds: StructBond[]; date: string }) {
  const data = useMemo(() => {
    const valid = bonds.filter(
      (b) => b.coupon !== null && b.coupon !== undefined && termYears(b.tenor) !== null,
    );
    const terms = valid.map((b) => termYears(b.tenor)!);
    const axis = buildTermAxis(terms.length ? Math.max(...terms) : 0);
    const points: Point[] = valid.map((b) => {
      const term = termYears(b.tenor)!;
      return {
        x: Number(axis.xMap(Math.min(term, MAX_YEAR)).toFixed(6)),
        y: Number(b.coupon!.toFixed(4)),
        z: b.amountWan ? b.amountWan / 10000 : 0,
        name: b.name,
        term,
        type: bondTypeShort(b.typeDesc),
      };
    });
    // 稳健纵轴：券数够多时按 P1~P99 定界，离群高/低票面券不入图（下方注脚说明），
    // 避免个别 5%+ 弱资质券把纵轴撑开、压扁主体分布。
    const sorted = points.map((p) => p.y).sort((a, b) => a - b);
    const pick = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))];
    const robust = points.length >= 30;
    const yLo = robust ? pick(0.01) : Math.min(...sorted);
    const yHi = robust ? pick(0.99) : Math.max(...sorted);
    const inliers = robust
      ? points.filter((p) => p.y >= yLo && p.y <= yHi)
      : points;
    const outliers = points.filter((p) => !inliers.includes(p));
    const groups = new Map<string, Point[]>();
    for (const p of inliers) {
      if (!groups.has(p.type)) groups.set(p.type, []);
      groups.get(p.type)!.push(p);
    }
    const byType = [...groups.entries()]
      .map(([type, pts]) => ({ type, color: TYPE_COLORS[type] ?? TYPE_COLORS["其他"], points: pts }))
      .sort((a, b) => b.points.length - a.points.length);
    // 分期限桶中位票面趋势线：让当日定价中枢一眼可见，散点围绕其分布
    const bucketBounds = [1, 2, 3, 5, 7, 10, 15, 30];
    const trend = bucketBounds
      .map((hi, i) => {
        const lo = i === 0 ? 0 : bucketBounds[i - 1];
        const inBucket = inliers.filter((p) => p.term > lo && p.term <= hi);
        if (inBucket.length < 3) return null;
        const ys = inBucket.map((p) => p.y).sort((a, b) => a - b);
        const median = ys[Math.floor(ys.length / 2)];
        return { x: Number(axis.xMap(Math.min(inBucket.reduce((s, p) => s + p.term, 0) / inBucket.length, MAX_YEAR)).toFixed(6)), y: median };
      })
      .filter((t): t is { x: number; y: number } => t !== null)
      .sort((a, b) => a.x - b.x);
    return { points, byType, yLo, yHi, outliers, trend, axis };
  }, [bonds]);

  if (!data.points.length) return null;

  const yPad = Math.max(0.08, (data.yHi - data.yLo) * 0.1);

  return (
    <div className="mb-4 rounded-xl border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <p className="text-sm font-semibold">
          期限结构散点
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            {date} · {data.byType.reduce((n, g) => n + g.points.length, 0)} 只已定价 · 颜色=类型、大小=规模
          </span>
        </p>
        <span className="text-[11px] text-muted-foreground">
          {data.axis.axisMax > 5
            ? "横轴 1~5Y 区间拉伸、5Y 后压缩，刻度为真实年限"
            : `横轴自适应当日发行期限（至 ${data.axis.axisMax}Y），前半区间拉伸`}
        </span>
      </div>
      <div className="p-4">
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 16, bottom: 4, left: 12 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.35} vertical={false} />
              <XAxis
                type="number"
                dataKey="x"
                domain={[0, 1]}
                ticks={data.axis.ticks}
                tickFormatter={(v) => data.axis.tickLabels.get(Number(Number(v).toFixed(6))) ?? ""}
                tick={{ fontSize: 11 }}
                tickMargin={4}
              />
              <YAxis
                type="number"
                dataKey="y"
                domain={[Number((data.yLo - yPad).toFixed(3)), Number((data.yHi + yPad).toFixed(3))]}
                tick={{ fontSize: 11 }}
                tickFormatter={(v) => v.toFixed(2)}
                width={52}
                label={{ value: "票面%", position: "insideLeft", angle: -90, dy: 40, fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
              />
              {/* 散点直径=规模 6~16px。ZAxis range 语义是面积（recharts 内部 radius=√(size/π)），故传 π·r² */}
              <ZAxis type="number" dataKey="z" range={[28, 200]} />
              <Tooltip content={<ScatterTooltip />} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {data.trend.length >= 2 && (
                <Scatter
                  name="中位票面"
                  data={data.trend}
                  line
                  stroke="#475569"
                  strokeDasharray="5 4"
                  strokeWidth={1.5}
                  fill="#475569"
                  legendType="plainline"
                  shape={() => <g />}
                  isAnimationActive={false}
                />
              )}
              {data.byType.map((g) => (
                <Scatter
                  key={g.type}
                  name={g.type}
                  data={g.points}
                  fill={g.color}
                  fillOpacity={0.6}
                  stroke="#ffffff"
                  strokeWidth={0.5}
                  isAnimationActive={false}
                />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        {data.outliers.length > 0 && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            纵轴按当日票面 P1~P99 显示；另有 {data.outliers.length} 只票面{" "}
            {Math.min(...data.outliers.map((p) => p.y)).toFixed(2)}%~{Math.max(...data.outliers.map((p) => p.y)).toFixed(2)}%
            的个券未入图，见下方明细表
          </p>
        )}
      </div>
    </div>
  );
}
