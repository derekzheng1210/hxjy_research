// DM API 客户端（Node 版）
// 协议：SM4-ECB 加密（PKCS#7 padding）+ base64url，与官方 Python SDK (dm_quant_api_client) 完全兼容
// 请求体: base64url(SM4_ECB_encrypt(JSON))
// 响应体: JSON 字符串值，内为 base64url(SM4_ECB_encrypt(明文JSON))
// 认证: header X-Dm-App-Key
import { sm4 } from "sm-crypto";

const BASE_URL = "https://gapi-ext.innodealing.com";

function getCredentials() {
  const key = process.env.INNO_APP_KEY;
  const secret = process.env.INNO_APP_SECRET;
  if (!key || !secret) {
    throw new Error("缺少 DM API 密钥：请设置环境变量 INNO_APP_KEY 与 INNO_APP_SECRET");
  }
  return { key, secret };
}

function prepareKey(secret: string): Buffer {
  const b = Buffer.from(secret, "utf8");
  if (b.length > 16) return b.slice(0, 16);
  if (b.length < 16) return Buffer.concat([b, Buffer.alloc(16 - b.length)]);
  return b;
}

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function encryptBody(data: unknown, secret: string): string {
  const json = JSON.stringify(data);
  const hex = sm4.encrypt(json, prepareKey(secret), { mode: "ecb" });
  return b64url(Buffer.from(hex, "hex"));
}

function decryptResponse(b64: string, secret: string): string {
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const encHex = Buffer.from(padded, "base64").toString("hex");
  // sm-crypto 的 decrypt 直接返回明文 UTF-8 字符串
  return sm4.decrypt(encHex, prepareKey(secret), { mode: "ecb" });
}

function camelToSnakeKey(k: string): string {
  return k.replace(/[A-Z]/g, (m) => "_" + m.toLowerCase());
}

// 递归转换 camelCase -> snake_case
export function camelToSnake<T>(obj: T): T {
  if (Array.isArray(obj)) return obj.map(camelToSnake) as unknown as T;
  if (obj !== null && typeof obj === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      out[camelToSnakeKey(k)] = camelToSnake(v);
    }
    return out as T;
  }
  return obj;
}

export interface DmResponse {
  code: number;
  message: string;
  data: unknown;
}

/**
 * 调用 DM API
 * @param data 请求参数（snake_case，SDK 会自动转 camelCase 发出去）
 * @param apiPath 接口路径
 */
export async function postData<T = unknown>(data: Record<string, unknown>, apiPath: string): Promise<T> {
  const { key, secret } = getCredentials();
  const body = encryptBody(data, secret);
  const res = await fetch(BASE_URL + apiPath, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Dm-App-Key": key,
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0 Safari/537.36",
    },
    body,
    cache: "no-store",
  });
  if (res.status !== 200) {
    throw new Error(`DM API HTTP ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  const text = await res.text();
  const b64 = JSON.parse(text) as string;
  const plain = decryptResponse(b64, secret);
  const obj = JSON.parse(plain) as DmResponse;
  if (obj.code !== 0) {
    throw new Error(`DM API error: ${obj.message || obj.code}`);
  }
  return camelToSnake<T>(obj.data as T);
}

// ==================== 业务封装 ====================

export const DM_PATHS = {
  primary: "/dm-quant-func-service/api/v1/bond/primary/data",
  yieldCurve: "/dm-quant-func-service/api/v1/bond/yield-curve/data",
  basicInfo: "/dm-quant-func-service/api/v1/bond/basic-info/info",
  outstandingBonds: "/dm-quant-func-service/api/v1/bond/basic-info/outstanding-bonds",
  marketDate: "/dm-quant-func-service/api/v1/market/trade-dates",
  companyRating: "/dm-quant-func-service/api/v1/company/rating/data",
} as const;

export interface PrimaryQuery {
  startDate: string;
  endDate: string;
  bondCategory?: number; // 1=信用债
  offset?: number;
  fieldNames?: string[];
}

export interface PrimaryResult {
  list: Record<string, unknown>[];
  max_offset?: number;
  maxOffset?: number;
  [key: string]: unknown;
}

/** 拉取一级发行数据（自动翻页） */
export async function fetchPrimaryAll(startDate: string, endDate: string, bondCategory = 1): Promise<Record<string, unknown>[]> {
  const rows: Record<string, unknown>[] = [];
  let offset = 0;
  for (let i = 0; i < 100; i++) {
    const res = await postData<PrimaryResult>(
      { startDate, endDate, bondCategory, offset },
      DM_PATHS.primary
    );
    const list = res?.list || [];
    rows.push(...list);
    const mo = res?.max_offset ?? res?.maxOffset;
    if (mo === undefined || mo === null || mo <= offset) break;
    offset = mo;
  }
  return rows;
}

/** 收益率曲线 */
export async function fetchYieldCurve(
  startDate: string,
  endDate: string,
  curveName = "中债国债收益率曲线",
  curveTermList = ["1", "3", "5", "7", "10"],
  curveType = "1"
): Promise<Record<string, unknown>[]> {
  const res = await postData<Record<string, unknown>[] | { list?: unknown[]; data?: unknown[] }>(
    { dataSource: "18", curveName, curveTermList, curveType, startDate, endDate },
    DM_PATHS.yieldCurve
  );
  if (Array.isArray(res)) return res as Record<string, unknown>[];
  return ((res?.list || res?.data || []) as Record<string, unknown>[]);
}

/** 债券基础资料（含上市日 list_date） */
export async function fetchBasicInfo(securityIds: string[]): Promise<Record<string, unknown>[]> {
  const res = await postData<Record<string, unknown>[] | { list?: unknown[]; data?: unknown[] }>(
    { securityIdList: securityIds }, // DM 接口要求 camelCase 参数名
    DM_PATHS.basicInfo
  );
  if (Array.isArray(res)) return res as Record<string, unknown>[];
  return ((res?.list || res?.data || []) as Record<string, unknown>[]);
}

/** 发行人维度存续债（除取消发行，默认取发行中+存续中，支持翻页） */
export async function fetchOutstandingBonds(
  issuerFullName: string,
  opts?: { bondStatusList?: number[]; offset?: number }
): Promise<{ list: Record<string, unknown>[]; maxOffset?: number }> {
  const res = await postData<Record<string, unknown>[] | { list?: unknown[]; max_offset?: number; maxOffset?: number }>(
    {
      issuerFullName, // DM 接口要求 camelCase 参数名
      bondStatusList: opts?.bondStatusList ?? [1, 2],
      offset: opts?.offset ?? 0,
    },
    DM_PATHS.outstandingBonds
  );
  const list = Array.isArray(res) ? (res as Record<string, unknown>[]) : ((res?.list || []) as Record<string, unknown>[]);
  const maxOffset = Array.isArray(res) ? undefined : (res?.max_offset ?? res?.maxOffset);
  return { list, maxOffset };
}

/**
 * 主体评级历史（外部评级 + 标普信评PCA + YY评分，带 rating_date 序列）
 * 不传日期区间 = 最近 5 年全量（实测传 startDate 反而返回 0 行，2026-09-18 探针验证）；
 * 单次最多 5 个主体全称。
 */
export async function fetchCompanyRatingHistory(
  comChiNameList: string[],
  opts?: { startDate?: string; endDate?: string }
): Promise<Record<string, unknown>[]> {
  const body: Record<string, unknown> = { comChiNameList }; // DM 接口要求 camelCase 参数名
  if (opts?.startDate) body.startDate = opts.startDate;
  if (opts?.endDate) body.endDate = opts.endDate;
  const res = await postData<Record<string, unknown>[] | { list?: unknown[]; data?: unknown[] }>(
    body,
    DM_PATHS.companyRating
  );
  if (Array.isArray(res)) return res as Record<string, unknown>[];
  return ((res?.list || res?.data || []) as Record<string, unknown>[]);
}
