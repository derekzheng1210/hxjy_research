"use client";

import { useState } from "react";
import { Sheet } from "lucide-react";
import { DataAdminDialog } from "@/components/data-admin";

/**
 * 「上传 Excel」入口按钮：点击直接打开数据管理面板
 * （每日发行 / 中标登记上传 + DM 手动刷新）。
 */
export function UploadExcelButton({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      {compact ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex h-8 items-center gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-2.5 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
        >
          <Sheet className="h-3.5 w-3.5 shrink-0" />
          上传 Excel
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex w-full items-center gap-2.5 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-2 text-[13px] font-medium text-primary transition-colors hover:bg-primary/10"
        >
          <Sheet className="h-4 w-4 shrink-0" />
          <span className="flex-1 text-left">上传 Excel</span>
          <span className="text-[10px] font-normal text-muted-foreground">每日发行 · 中标登记</span>
        </button>
      )}

      {open && <DataAdminDialog open={open} onOpenChange={setOpen} />}
    </>
  );
}
