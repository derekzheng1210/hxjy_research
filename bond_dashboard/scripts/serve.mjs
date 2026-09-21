// Bond Dashboard · 分离式启动器
// -------------------------------------------------------------------------
// 为什么需要它：直接用 `next start` 起服务时，进程会挂在启动它的终端/控制台上，
// 终端一关（或 Agent 会话结束、计划任务实例结束）进程就收到 CTRL_C / CTRL_CLOSE 被杀，
// 表现为"每次更新后页面数据打不开"。
// 做法：本脚本 spawn 一个 detached 的 next start（独立进程组、无窗口、输出重定向到
// server.log），随后自己立刻退出，服务进程不再依赖任何父终端。
// 用法: node scripts/serve.mjs   （重复执行时若 3000 已在监听则直接退出）
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const NEXT_BIN = path.join(ROOT, "node_modules", "next", "dist", "bin", "next");
const LOG = path.join(ROOT, "server.log");
// 门户集成：PORT/HOST 可由环境变量下发（门户反向代理默认 127.0.0.1:3100，
// 绑定回环避免绕过门户鉴权直连）；独立运行时保持原默认 0.0.0.0:3000
const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || "0.0.0.0";

function isPortBusy(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", (e) => resolve(e.code === "EADDRINUSE"));
    srv.once("listening", () => srv.close(() => resolve(false)));
    srv.listen(port, "0.0.0.0");
  });
}

if (await isPortBusy(PORT)) {
  console.log(JSON.stringify({ ok: true, started: false, reason: "port_in_use" }));
  process.exit(0);
}

const out = fs.openSync(LOG, "a");
const child = spawn(process.execPath, [NEXT_BIN, "start", "-p", String(PORT), "-H", HOST], {
  cwd: ROOT,
  detached: true,
  windowsHide: true,
  stdio: ["ignore", out, out],
  env: { ...process.env, NODE_OPTIONS: "" },
});
child.unref();
console.log(JSON.stringify({ ok: true, started: true, pid: child.pid }));
process.exit(0);
