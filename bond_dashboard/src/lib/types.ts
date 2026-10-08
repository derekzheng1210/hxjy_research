// 看板核心类型定义

// 投标记录（参与 / 中标）
export type BidType = "participated" | "won";

export interface BidRecord {
  id: string;
  type: BidType; // participated=参与投标, won=中标
  bondName: string; // 债券简称
  securityId?: string; // 债券代码
  issuer?: string; // 发行人
  term?: string; // 期限
  amount: number; // 金额（亿元）
  coupon?: number; // 票面利率 %
  spread?: number; // 票面-预测（bp）
  yy?: string; // YY 评分
  bidDate: string; // 投标日期 YYYY-MM-DD
  payDate?: string; // 缴款日
  listDate?: string; // 上市日（来自 DM 或估算）
  note?: string; // 备注
  author?: string; // 录入人显示名（Excel 导入/脚本录入为空）
  couponNote?: string | null; // 票面缺失原因标注（取消发行 / 回拨X年 / 未截标），由 /api/bids 注入
  internalRating?: string | null; // 主体内评（信评门户数据，由 /api/bids 按发行人富化）
  createdAt: string; // 创建时间 ISO
}

// 信息交流栏留言
export interface Message {
  id: string;
  author: string; // 昵称
  content: string; // 内容
  createdAt: string; // ISO
}

// DM 一级发行债券（snake_case，来自 DM API）
export interface DmBond {
  sec_short_name: string; // 债券简称
  security_id?: string; // 债券代码
  issue_start_date?: string; // 发行起始日
  issue_end_date?: string;
  subscribe_date?: string; // 截标日
  subscribe_time?: string; // 截标时间
  bond_category?: number;
  bond_category_desc?: string;
  bond_type?: string;
  bond_type_desc?: string; // 债券类型
  issuer_full_name?: string; // 发行人
  issuer_yy?: string | null; // YY 评分（rating=1 时由 DM company/rating/data 补全）
  province_name?: string; // 区域-省
  city_name?: string; // 区域-市
  bond_issue_tenor?: string; // 期限
  bond_matu_struct?: string; // 含权结构
  plan_issue_amount?: number; // 计划发行（万元）
  actu_issue_amount?: number; // 实际发行（万元）
  issue_yield?: number; // 票面利率 %
  subscribe_rate?: string; // 投标区间
  weighted_rate?: number; // 加权收益率
  marginal_rate?: number; // 边际收益率
  compliant_subscription_mult?: number; // 全场倍数
  marginal_mult?: number; // 边际倍数
  issue_price_forecast?: number; // 新债预测 %
  similar_bond_code?: string; // 可比二级券代码
  similar_bond_short_name?: string; // 可比二级券简称
  similar_bond_remaining_tenor?: string; // 可比券剩余期限
  similar_bond_cb_valuation?: number; // 二级估值 %
  similar_bond_bid_price?: number;
  similar_bond_ofr_price?: number;
  pay_date?: string; // 缴款日
  listed_date_add?: string | null; // 上市日（DM 字段，常为空）
  issue_status?: string;
  issue_status_desc?: string; // 发行状态
  public_offering_status?: string;
  gura_name?: string; // 担保人
  unde_name?: string; // 承销商
  lead_underwriter_bal_amount?: number; // 主承销商余额包销
  lead_underwriter_bal_ratio?: number;
  proceeds_use?: string; // 募集用途
  fund_use_type_desc?: string;
  coupon_type_desc?: string;
  internalRating?: string | null; // 主体内评（信评门户数据，由 /api/dm/primary 按发行人富化）
  [key: string]: unknown;
}

// DM 基础资料（含上市日 list_date）
export interface DmBondBasic {
  security_id?: string;
  sec_short_name?: string;
  list_date?: string | null; // 上市日
  iss_start_date?: string; // 发行起始日
  inte_start_date?: string; // 计息日
  matu_pay_date?: string; // 到期日
  [key: string]: unknown;
}

// ===== Excel《一级发行-信用债发行》每日清单口径 =====
export interface ExcelBond {
  name: string; // 债券简称
  amountYi: number; // 计划发行(亿)
  type?: string | null;
  tenor?: string | null; // 发行期限
  issuer?: string | null; // 发行人
  yy?: string | null;
  forecast?: number | null; // 新债预测
  coupon?: number | null; // 票面
  payDate?: string | null; // 缴款日
  recommended?: boolean; // 是否推荐
  internalRating?: string | null; // 主体内评（信评门户数据，由 /api/excel-calendar 按发行人富化）
  pledge?: IssuerPledgeRange | null; // 交易所质押比区间（issuer_metrics 快照，服务端富化）
  yyAdj?: IssuerYyAdj | null; // 近五年 YY 调整（issuer_metrics 快照，服务端富化）
}

// 发行人补充指标（issuer_metrics.json，由打包者机 Python 管道生成）
export interface IssuerPledgeRange {
  min: number;
  max: number;
  n: number;
}

export interface IssuerYyAdj {
  up: { from: string; to: string; date: string }[];
  down: { from: string; to: string; date: string }[];
  yyNet: {
    dir: "up" | "down" | "flat";
    steps: number;
    from: string;
    to: string;
    firstDate: string;
    lastDate: string;
    moves: number;
    firstDir: "up" | "down" | null;
  } | null;
}

export interface ExcelCalendarDay {
  file: string;
  count: number;
  planYi: number;
  bonds: ExcelBond[];
}

export interface ExcelCalendarSummary {
  date: string; // YYYY-MM-DD
  count: number;
  planYi: number;
}
