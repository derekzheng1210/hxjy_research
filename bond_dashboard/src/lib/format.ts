// 通用工具：格式化、日期、计算

/** 万元 -> 亿元，保留 n 位小数 */
export function wanToYi(wan: number | null | undefined, digits = 2): string {
  if (wan === null || wan === undefined || Number.isNaN(wan)) return "-";
  const yi = wan / 10000;
  return yi.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 数字格式化（亿元保留2位） */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 百分比保留 2 位 */
export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toFixed(2) + "%";
}

/** 利差 (bp)：预测 - 二级估值，保留 1 位 */
export function spreadBp(forecast: number | null | undefined, secVal: number | null | undefined): number | null {
  if (forecast === null || forecast === undefined || secVal === null || secVal === undefined) return null;
  if (Number.isNaN(forecast) || Number.isNaN(secVal)) return null;
  return Math.round((forecast - secVal) * 1000) / 10;
}

/** 票面 - 预测利差 (bp) */
export function couponSpreadBp(coupon: number | null | undefined, forecast: number | null | undefined): number | null {
  if (coupon === null || coupon === undefined || forecast === null || forecast === undefined) return null;
  if (Number.isNaN(coupon) || Number.isNaN(forecast)) return null;
  return Math.round((coupon - forecast) * 1000) / 10;
}

/** 日期格式化 YYYY-MM-DD -> M月D日 周X */
export function fmtDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  const week = ["日", "一", "二", "三", "四", "五", "六"][d.getDay()];
  return `${d.getMonth() + 1}月${d.getDate()}日 周${week}`;
}

export function fmtDateShort(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** 获取最近 N 个工作日（跳过周末；节假日暂不做，可用 trade-dates 接口增强） */
export function recentWorkdays(count: number, from?: Date): string[] {
  const days: string[] = [];
  const d = from ? new Date(from) : new Date();
  d.setHours(12, 0, 0, 0); // 避免时区问题
  while (days.length < count) {
    const day = d.getDay();
    if (day !== 0 && day !== 6) {
      days.push(d.toISOString().slice(0, 10));
    }
    d.setDate(d.getDate() - 1);
  }
  return days;
}

/** 上一工作日 */
export function prevWorkday(from?: Date): string {
  return recentWorkdays(2, from)[1];
}

/** 下一工作日 */
export function nextWorkday(from?: Date): string {
  const d = from ? new Date(from) : new Date();
  d.setHours(12, 0, 0, 0);
  do {
    d.setDate(d.getDate() + 1);
  } while (d.getDay() === 0 || d.getDay() === 6);
  return d.toISOString().slice(0, 10);
}

/** 距上市日天数（负数=已过） */
export function daysUntil(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  d.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - now.getTime()) / 86400000);
}

/** 期限分桶 */
export function termBucket(term: string | null | undefined): string {
  if (!term) return "未知";
  const s = String(term);
  if (s.includes("N")) return "永续";
  const m = s.match(/^([\d.]+)\s*(Y|D)?/);
  if (!m) return "未知";
  const num = parseFloat(m[1]);
  const unit = m[2] || "Y";
  const y = unit === "D" ? num / 365 : num;
  if (y < 1) return "<1Y";
  if (y < 3) return "1-3Y";
  if (y < 5) return "3-5Y";
  if (y < 10) return "5-10Y";
  return ">=10Y";
}

/** 债券类型缩写（海报风格分类） */
export function bondTypeShort(typeDesc: string | null | undefined): string {
  if (!typeDesc) return "其他";
  const t = typeDesc;
  if (t.includes("超短")) return "SCP";
  // CP 口径：除"短融"外，DM 类型还有"短期融资券/短期融资券(CP)/证券公司短期融资券"
  if (t.includes("短融") || t.includes("短期融资")) return "CP";
  if (t.includes("中期票据")) return "MTN";
  if (t.includes("定向")) return "PPN";
  if (t.includes("公司债")) return "公司债";
  if (t.includes("企业债")) return "企业债";
  if (t.includes("二级资本")) return "二级资本债";
  if (t.includes("永续")) return "永续债";
  if (t.includes("商业银行")) return "商行债";
  if (t.includes("可转")) return "可转债";
  if (t.includes("可交换")) return "可交换债";
  if (t.includes("资产支持")) return "ABS";
  return "其他";
}

/** 估算上市日：缴款日 + 2 个工作日（信用债一般缴款后第 2 个交易日上市） */
export function estimateListDate(payDate: string | null | undefined): string | null {
  if (!payDate) return null;
  const d = new Date(payDate);
  if (Number.isNaN(d.getTime())) return null;
  d.setHours(12, 0, 0, 0);
  let added = 0;
  while (added < 2) {
    d.setDate(d.getDate() + 1);
    if (d.getDay() !== 0 && d.getDay() !== 6) added++;
  }
  return d.toISOString().slice(0, 10);
}

/** 文本截断 */
export function truncate(s: string | null | undefined, len: number): string {
  if (!s) return "-";
  return s.length > len ? s.slice(0, len) + "…" : s;
}
