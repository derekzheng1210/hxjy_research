"use client";

import { useCallback, useState } from "react";
import { PageHeader } from "@/components/page-header";
import { BidList } from "@/components/bid-list";
import { BidFormDialog } from "@/components/bid-form";

export default function WonPage() {
  const [refreshKey, setRefreshKey] = useState(0);
  const onSaved = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <>
      <PageHeader
        title="中标个券"
        description="管理中标个券记录，上市提醒将基于此数据自动计算"
      >
        <BidFormDialog defaultType="won" onSaved={onSaved} />
      </PageHeader>
      <div key={refreshKey}>
        <BidList type="won" />
      </div>
    </>
  );
}
