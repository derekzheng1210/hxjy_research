#!/usr/bin/env node
// fetch 前缀批量改造脚本（方案B basePath 配套）
// 背景：next.config.ts 配置 basePath 后，<Link>/路由自动带前缀，
//       但代码里手写的 fetch("/api/...") 不会自动带，必须统一改为 apiFetch(...)。
// 本脚本将 src 下所有 fetch("/api/...") / fetch(`/api/...`（含模板串）改为 apiFetch(...)，
// 并在文件头自动插入 import。幂等可重复执行，已改造过的文件不会重复插入。
//
// 用法：node scripts/apply_base_path_prefix.mjs [项目根目录（默认当前目录）]
// 同事在完整项目上套用 basePath 改造时：先加 next.config.ts 的 basePath 与
// src/lib/base-path.ts（见《方案B改造说明.md》），再运行本脚本即可。
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(process.argv[2] || process.cwd());
const srcDir = path.join(root, "src");
const IMPORT = 'import { apiFetch } from "@/lib/base-path";';

const files = [];
(function walk(dir) {
  if (!fs.existsSync(dir)) return;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(ts|tsx)$/.test(e.name)) files.push(p);
  }
})(srcDir);

let changed = 0;
for (const f of files) {
  if (f.replace(/\\/g, "/").endsWith("src/lib/base-path.ts")) continue; // 不改自己
  let src = fs.readFileSync(f, "utf8");
  const before = src;
  // 仅替换紧跟 /api 字面量的 fetch(（外部 URL 的 fetch 不受影响）
  src = src.replace(/fetch\((["'`])\/api/g, "apiFetch($1/api");
  if (src === before) continue;
  if (!src.includes('from "@/lib/base-path"')) {
    const lines = src.split("\n");
    let i = 0;
    while (i < lines.length && lines[i].trim() === "") i++;
    if (i < lines.length && /^\s*["']use client["'];?\s*$/.test(lines[i])) {
      // "use client" 必须保持首行，import 插在其后
      lines.splice(i + 1, 0, "", IMPORT);
    } else {
      lines.splice(i, 0, IMPORT, "");
    }
    src = lines.join("\n");
  }
  fs.writeFileSync(f, src, "utf8");
  changed++;
  console.log("已改造:", path.relative(root, f));
}
console.log(`\n完成：${changed} 个文件已改为 apiFetch(...)`);
console.log('提示：后续新增 API 调用请直接写 apiFetch("/api/...")（来自 @/lib/base-path）');
