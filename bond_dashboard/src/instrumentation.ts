// 服务器启动钩子（Next.js 文件约定：src/instrumentation.ts 的 register 在服务启动时执行一次）
// 用途：注册 DM 每日自动刷新调度器（详见 src/lib/dm-refresh.ts）
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { startRefreshScheduler } = await import("./lib/dm-refresh");
  startRefreshScheduler();
}
