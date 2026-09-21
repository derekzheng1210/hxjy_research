"use client";

import { useCallback, useState } from "react";
import { PageHeader } from "@/components/page-header";
import { BidList } from "@/components/bid-list";
import { BidFormDialog } from "@/components/bid-form";

export default function ParticipatedPage() {
  const [refreshKey, setRefreshKey] = useState(0);
  const onSaved = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <>
      <PageHeader
        title="参与个券"
        description="记录你参与投标的个券（可后续标记中标）"
      >
        <BidFormDialog defaultType="participated" onSaved={onSaved} />
      </PageHeader>
      <div key={refreshKey}>
        <BidList type="participated" />
      </div>
      <div className="mt-6">
        <p className="mb-2 text-sm font-semibold">操作提示</p>
        <div className="rounded-xl border bg-muted/30 p-4 text-[13px] text-muted-foreground">
          <p>1. 点击右上角「录入参与记录」，填写参与投标的债券信息；</p>
          <p>2. 中标后可在「中标个券」页面录入中标记录（或删除参与记录）；</p>
          <p>3. 已录入的缴款日将自动用于「中标上市提醒」的上市日估算。</p>
        </div>
      </div>
    </>
  );
}
