// 信用债口径清洗规则（《债券清洗工作流》）
import type { DmBond } from "./types";

/**
 * 是否含永续结构。
 * 注意：DM 的续期标志实际藏在 `bond_matu_struct`（如 `5Y(5+N)`、`3Y+N`）里，
 * 旧实现只检查 `bond_issue_tenor`（值形如 `5Y`）会漏剔公募永续债，故两者都判。
 */
export function isPerpetual(
  b: { bond_matu_struct?: string | null; bond_issue_tenor?: string | null; sec_short_name?: string | null }
): boolean {
  const s = `${b.bond_matu_struct ?? ""} ${b.bond_issue_tenor ?? ""}`;
  if (/\+\s*N/i.test(s)) return true;
  if (s.includes("永续")) return true;
  return String(b.sec_short_name ?? "").includes("永续");
}

/** 是否为 PPN（定向工具）。DM 的 bond_type_desc 通常为「定向工具」而非 "PPN"，需两者都判 */
export function isPPN(
  b: { bond_type_desc?: string | null; sec_short_name?: string | null }
): boolean {
  const type = String(b.bond_type_desc ?? "");
  if (type.includes("PPN")) return true;
  if (type.includes("定向")) return true; // 定向工具/定向债务融资工具 = PPN
  const name = String(b.sec_short_name ?? "");
  if (name.includes("PPN")) return true; // 券简称形如 26XXXPPN001
  return false;
}

/** 是否为私募债：283/520 前缀代码，或 DM 发行方式标「私募」 */
export function isPrivatePlacement(
  b: { security_id?: string | null; public_offering_status?: string | null }
): boolean {
  if (/^(283|520)/.test(String(b.security_id ?? ""))) return true;
  return String(b.public_offering_status ?? "").includes("私募");
}

/**
 * 是否为可转债 / 可转换债券。
 * 注意：DM 的 `bond_type_desc` 写作「可转换债券」（历史全量 282 只），
 * 仅匹配子串 "可转债" 会全部漏剔——"可转换债券" 里并无连续的 "可转债" 三字。
 * 名称层只作兜底，且必须用严格模式（转债 / 转02 / 以「转」结尾）；
 * 不能用裸 "转"（会误伤 25转型K1、26中远海发MTN002(转型) 这类含「转型」的普通信用债），
 * 也不能用 "定\d+$"（会误伤 25嘉定01 这类名字含「定01」的城投公司债）。
 */
export function isConvertible(
  b: { bond_type_desc?: string | null; sec_short_name?: string | null }
): boolean {
  const type = String(b.bond_type_desc ?? "");
  if (type.includes("可转")) return true; // 可转债 / 可转换债券 / 可转换公司债券
  const name = String(b.sec_short_name ?? "");
  if (name.includes("转债")) return true; // XX转债
  if (/转\d*$/.test(name)) return true; // 润禾转02、精测转2、鸿盛定转
  return false;
}

/** 是否为可交换债（DM 写作「可交换债券」，同理不能用 "可交换债" 死匹配） */
export function isExchangeable(
  b: { bond_type_desc?: string | null; sec_short_name?: string | null }
): boolean {
  const type = String(b.bond_type_desc ?? "");
  if (type.includes("可交换")) return true;
  return String(b.sec_short_name ?? "").includes("交换债");
}

/** 返回剔除原因或 null */
export function cleanReason(
  b: Pick<DmBond, "bond_issue_tenor" | "bond_matu_struct" | "bond_type_desc" | "security_id" | "sec_short_name" | "public_offering_status">
): string | null {
  if (isPerpetual(b)) return "永续债";
  if (isPPN(b)) return "PPN";
  if (isExchangeable(b)) return "可交换债";
  if (isConvertible(b)) return "可转债";
  if (isPrivatePlacement(b)) return "私募债";
  return null;
}

/** 过滤出信用债口径个券 */
export function cleanCreditBonds<T extends DmBond>(rows: T[]): T[] {
  return rows.filter((b) => !cleanReason(b));
}

/**
 * 只保留截标日（簿记日）= 目标日的个券。
 * DM 一级发行接口在单日查询时会混入上一交易日簿记的券，需按 `subscribe_date` 对齐。
 */
export function sameBookDate<T extends { subscribe_date?: string | null }>(rows: T[], date: string): T[] {
  return rows.filter((b) => String(b.subscribe_date ?? "").trim() === date);
}
