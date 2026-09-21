import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

const ACCENT: Record<string, string> = {
  blue: "border-blue-200 bg-blue-50/60 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-400",
  green: "border-emerald-200 bg-emerald-50/60 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-400",
  amber: "border-amber-200 bg-amber-50/60 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-400",
  red: "border-rose-200 bg-rose-50/60 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-400",
  violet: "border-violet-200 bg-violet-50/60 text-violet-700 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-400",
};

export function StatCard({
  label,
  value,
  sub,
  icon: Icon,
  accent = "blue",
}: {
  label: string;
  value: string | number;
  sub?: string;
  icon: LucideIcon;
  accent?: "blue" | "green" | "amber" | "red" | "violet";
}) {
  return (
    <div className={cn("rounded-xl border p-4", ACCENT[accent])}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium opacity-80">{label}</p>
        <Icon className="h-4 w-4 opacity-70" />
      </div>
      <p className="mt-2 text-2xl font-bold tracking-tight">{value}</p>
      {sub && <p className="mt-1 text-[11px] opacity-70">{sub}</p>}
    </div>
  );
}
