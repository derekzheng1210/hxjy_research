"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  LayoutDashboard,
  CalendarDays,
  ClipboardList,
  PenLine,
  Trophy,
  BellRing,
  MessageSquare,
  TrendingUp,
  GitCompareArrows,
  BarChart3,
  Landmark,
  Star,
  Building2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/base-path";
import { UploadExcelButton } from "@/components/upload-excel-button";
import { InstallPwaButton } from "@/components/install-pwa-button";

const NAV = [
  { group: "核心栏目", items: [
    { href: "/", label: "总览仪表盘", icon: LayoutDashboard },
    { href: "/daily", label: "每日一级发行", icon: CalendarDays },
    { href: "/results", label: "历史发行情况", icon: ClipboardList },
    { href: "/recommended", label: "推荐个券", icon: Star },
    { href: "/participated", label: "参与个券", icon: PenLine },
    { href: "/won", label: "中标个券", icon: Trophy },
    { href: "/listing", label: "中标上市提醒", icon: BellRing },
    { href: "/exchange", label: "信息交流栏", icon: MessageSquare },
  ]},
  { group: "分析增强", items: [
    { href: "/yield-curve", label: "收益率曲线", icon: TrendingUp },
    { href: "/spread", label: "利差分析", icon: GitCompareArrows },
    { href: "/trends", label: "历史趋势", icon: BarChart3 },
    { href: "/issuer", label: "发行人分析", icon: Building2 },
  ]},
];

const UNREAD_KEY = "bond-dashboard:exchange:last-read";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [unread, setUnread] = useState(0);

  // 拉取消息总数，与本地「上次已读的最新消息时间」对比得出未读数
  const refreshUnread = useCallback(async () => {
    try {
      const res = await apiFetch("/api/messages");
      if (!res.ok) return;
      const j = await res.json();
      const list: { id: string; createdAt: string }[] = j.list ?? [];
      if (!list.length) {
        setUnread(0);
        return;
      }
      const lastRead = localStorage.getItem(UNREAD_KEY);
      // 消息倒序（最新在前），未读 = 晚于「已读标记」新增的条数
      const latest = list[0]?.createdAt ?? "";
      if (!lastRead) {
        // 首次进入：全部视为已读基准，不显示角标
        localStorage.setItem(UNREAD_KEY, latest);
        setUnread(0);
        return;
      }
      const count = list.filter((m) => m.createdAt > lastRead).length;
      setUnread(count);
    } catch {
      /* 忽略轮询错误 */
    }
  }, []);

  // 进入信息交流栏时标记已读、清除角标
  useEffect(() => {
    if (pathname === "/exchange") {
      apiFetch("/api/messages")
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          const list: { createdAt: string }[] = j?.list ?? [];
          if (list.length) localStorage.setItem(UNREAD_KEY, list[0].createdAt);
        })
        .catch(() => {});
      setUnread(0);
    }
  }, [pathname]);

  // 定时轮询未读
  useEffect(() => {
    refreshUnread();
    const timer = setInterval(refreshUnread, 30000);
    return () => clearInterval(timer);
  }, [refreshUnread]);

  return (
    <div className="flex min-h-screen bg-muted/30">
      {/* 侧边栏 */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r bg-background lg:flex">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Landmark className="h-4.5 w-4.5" />
          </div>
          <div>
            <p className="text-sm font-bold leading-tight">债用债一级投资看板</p>
            <p className="text-[11px] leading-tight text-muted-foreground">Bond Primary Dashboard</p>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {NAV.map((section) => (
            <div key={section.group} className="mb-5">
              <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {section.group}
              </p>
              <ul className="space-y-0.5">
                {section.items.map((item) => {
                  const active = pathname === item.href;
                  const Icon = item.icon;
                  const showBadge = item.href === "/exchange" && unread > 0;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        className={cn(
                          "relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium transition-colors",
                          active
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-accent hover:text-foreground"
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0" />
                        {item.label}
                        {showBadge && (
                          <span
                            className={cn(
                              "absolute right-1.5 top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold leading-none",
                              "bg-red-500 text-white"
                            )}
                          >
                            {unread > 99 ? "99+" : unread}
                          </span>
                        )}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        <div className="space-y-2 border-t px-4 py-3">
          <InstallPwaButton />
          <UploadExcelButton />
        </div>
      </aside>

      {/* 移动端顶栏 */}
      <div className="fixed inset-x-0 top-0 z-40 flex h-14 items-center gap-2 border-b bg-background px-4 lg:hidden">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Landmark className="h-4.5 w-4.5" />
        </div>
        <p className="flex-1 truncate text-sm font-bold">债用债一级投资看板</p>
        <div className="flex justify-end">
          <UploadExcelButton compact />
        </div>
      </div>

      {/* 主区 */}
      <main className="flex-1 lg:pl-60">
        <div className="px-4 pt-16 pb-10 sm:px-6 lg:px-8 lg:pt-6">{children}</div>
      </main>
    </div>
  );
}
