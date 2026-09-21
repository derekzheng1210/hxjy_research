import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/status-state";
import { type CurveBucket } from "@/components/coupon-curve-chart";
import { YieldCurvePanel, type CurveMeta, type DateDataset } from "@/components/yield-curve-panel";

export const metadata = { title: "收益率曲线 · 债券一级投资看板" };
// 曲线 JSON 由服务器每晚 DM 刷新重建：必须每次请求时读盘，不能吃构建时的预渲染快照
export const dynamic = "force-dynamic";

export default function YieldCurvePage() {
  let meta: CurveMeta | null = null;
  let buckets: CurveBucket[] = [];
  let dataset: DateDataset | null = null;
  let loadError = "";
  try {
    const dir = DATA_DIR;
    const baseP = path.join(dir, "coupon_curve.json");
    const datesP = path.join(dir, "coupon_curve_by_date.json");
    if (fs.existsSync(baseP)) {
      const d = JSON.parse(fs.readFileSync(baseP, "utf8")) as {
        meta?: CurveMeta;
        buckets?: CurveBucket[];
      };
      meta = d.meta ?? null;
      buckets = d.buckets ?? [];
    }
    if (fs.existsSync(datesP)) {
      dataset = JSON.parse(fs.readFileSync(datesP, "utf8")) as DateDataset;
    }
  } catch (e) {
    loadError = e instanceof Error ? e.message : "数据读取失败";
  }

  const hasBase = !!meta && buckets.length > 0;

  return (
    <>
      <PageHeader
        title="收益率曲线"
        description="信用债一级发行票面·期限曲线（YY 评分 1-5 档）· 2026 年以来累计 · 数据源 DM 一级发行主数据"
      />

      {!hasBase && !dataset ? (
        <EmptyState
          title="暂无曲线数据"
          description={
            loadError || "缺少 data/coupon_curve.json，请先运行 scripts/build_coupon_curve.py。"
          }
        />
      ) : (
        <YieldCurvePanel meta={meta} baseBuckets={buckets} dataset={dataset} />
      )}
    </>
  );
}
