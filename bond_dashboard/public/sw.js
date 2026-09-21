/* 债券一级投资看板 Service Worker
 * 策略：
 *  - /_next/static、/icons 等带 hash/版本化静态资源：缓存优先（stale-while-revalidate）
 *  - 页面文档（导航）：网络优先，离线回退缓存首页
 *  - /api/* 一律网络（不缓存动态数据）
 * 版本号改动即整体更新缓存。
 * basePath 说明：SW 由 /bond-dashboard/sw.js 提供，作用域即 /bond-dashboard/，
 * 前缀从自身路径动态推导（BASE），不硬编码，便于独立部署与反代两种形态共用。
 */
const VERSION = "bond-dashboard-v1.1.0";
const CACHE_STATIC = `${VERSION}-static`;
const CACHE_DOC = `${VERSION}-doc`;
// 本 SW 的部署路径形如 `${BASE}/sw.js`，据此推导应用前缀（根部署时为空串）
const BASE = self.location.pathname.replace(/\/sw\.js$/, "");

self.addEventListener("install", (event) => {
  // 预缓存应用壳（首页文档），等待完成
  event.waitUntil(
    caches
      .open(CACHE_DOC)
      .then((c) => c.add(BASE + "/"))
      .catch(() => {})
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 跨域(字体/企微等)不处理
  if (url.pathname.startsWith(BASE + "/api/")) return; // 动态数据一律走网络

  // 带内容哈希的静态资源：缓存优先
  if (
    url.pathname.startsWith(BASE + "/_next/static/") ||
    url.pathname.startsWith(BASE + "/icons/")
  ) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const fetchAndCache = () =>
          fetch(req).then((res) => {
            if (res && res.ok) {
              const copy = res.clone();
              caches.open(CACHE_STATIC).then((c) => c.put(req, copy));
            }
            return res;
          });
        return hit || fetchAndCache();
      })
    );
    return;
  }

  // 其余同源请求（页面导航/文档）：网络优先，失败回退缓存
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && url.pathname.startsWith(BASE + "/")) {
          const copy = res.clone();
          caches.open(CACHE_DOC).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((hit) => hit || caches.match(BASE + "/"))
      )
  );
});
