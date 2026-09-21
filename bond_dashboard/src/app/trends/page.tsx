import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { PageHeader } from "@/components/page-header";
import {
  MonthlyChart,
  TypeDistChart,
  TermDistChart,
  YieldHistChart,
  MonthlyYieldChart,
  ProvinceTopChart,
} from "@/components/trends-charts";
import { EmptyState } from "@/components/status-state";
import type { TrendData } from "@/lib/types";

export const metadata = { title: "历史趋势 · 债用债一级投资看板" };
// 数据文件由刷新管道更新：每次请求读盘，避免构建时预渲染成静态快照
export const dynamic = "force-dynamic";

export default function TrendsPage() {
  let data: TrendData | null = null;
  let loadError = "";
  try {
    const p = path.join(DATA_DIR, "history_trends.json");
    if (fs.existsSync(p)) {
      data = JSON.parse(fs.readFileSync(p, "utf8")) as TrendData;
    }
  } catch (e) {
    loadError = e instanceof Error ? e.message : "数据读取失败";
  }

  return (
    <>
      <PageHeader
        title="历史趋势"
        description={`信用债一级发行历史统计（${data?.meta.range ?? "2023.01 ~ 2026.06"}）`}
      />
      {!data ? (
        <EmptyState title="暂无历史数据" description={loadError || "缺少 data/history_trends.json，请先运行数据预处理脚本。"} />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <div className="rounded-xl border p-4">
              <p className="text-xs text-muted-foreground">数据时间跨度</p>
              <p className="mt-1 text-lg font-bold">{data.meta.range}</p>
            </div>
            <div className="rounded-xl border p-4">
              <p className="text-xs text-muted-foreground">数据源</p>
              <p className="mt-1 text-lg font-bold">DM 一级发行历史库</p>
            </div>
            <div className="rounded-xl border p-4">
              <p className="text-xs text-muted-foreground">最新月度发行</p>
              <p className="mt-1 text-lg font-bold tabular-nums">
                {data.monthly.length ? `${data.monthly[data.monthly.length - 1].cnt} 只` : "-"}
              </p>
            </div>
            <div className="rounded-xl border p-4">
              <p className="text-xs text-muted-foreground">最新月度规模</p>
              <p className="mt-1 text-lg font-bold tabular-nums">
                {data.monthly.length ? `${Math.round(data.monthly[data.monthly.length - 1].plan).toLocaleString()} 亿` : "-"}
              </p>
            </div>
          </div>

          <div className="space-y-4">
            <MonthlyChart data={data} />
            <TypeDistChart data={data} />
            <TermDistChart data={data} />
            <YieldHistChart data={data} />
            <MonthlyYieldChart data={data} />
            <ProvinceTopChart data={data} />
          </div>
        </>
      )}
    </>
  );
}
