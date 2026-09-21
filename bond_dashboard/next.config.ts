import type { NextConfig } from "next";

// 方案B：门户反向代理部署前缀。
// 与 src/lib/base-path.ts 的 BASE_PATH 保持一致（构建期固化，改动需重新 build）。
const BASE_PATH = "/bond-dashboard";

const nextConfig: NextConfig = {
  basePath: BASE_PATH,
  // better-sqlite3 为原生模块：交给 Node 运行时直接 require，不打进产物 bundle
  serverExternalPackages: ["better-sqlite3"],
};

export default nextConfig;
