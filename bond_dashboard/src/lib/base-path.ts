// basePath 前缀（方案B：门户反向代理部署）
// 用途：Next.js 配置 basePath 后，<Link>/路由跳转自动带前缀，
// 但代码里手写的 fetch("/api/...") 不会自动带 —— 统一用 apiFetch/apiUrl 包装。
// 注意：此值必须与 next.config.ts 的 basePath 保持一致（basePath 为构建期固化值）。
export const BASE_PATH = "/bond-dashboard";

/** 拼接 API 相对路径（如 apiUrl("/api/bids") → "/bond-dashboard/api/bids"） */
export function apiUrl(path: string): string {
  return BASE_PATH + path;
}

/** fetch 包装：自动加 basePath 前缀，参数语义与原生 fetch 一致 */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init);
}
