/* OA 报销助手 · iOS PWA Service Worker
 * 作用域限定在 /m/（由 /m/sw.js 部署路径决定），只接管移动端相关资源，
 * 桌面端（/、/api/、/upload 等）的请求一律直接放行，互不干扰。
 */
var CACHE = 'oa-mobile-v1';
var SHELL = [
    '/m',
    '/static/mobile.css',
    '/static/mobile.js',
    '/static/icon-512.png',
    '/manifest.webmanifest'
];

self.addEventListener('install', function (e) {
    e.waitUntil(
        caches.open(CACHE).then(function (c) { return c.addAll(SHELL); })
            .then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (e) {
    e.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                .map(function (k) { return caches.delete(k); }));
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function (e) {
    var req = e.request;
    var url = new URL(req.url);

    // 仅处理同域 GET；桌面端流量（非 /m、非 /static、非 manifest）直接放行
    if (req.method !== 'GET' || url.origin !== self.location.origin) return;
    var p = url.pathname;
    if (p.indexOf('/api/') === 0) return;            // 接口走网络，不缓存
    if (p.indexOf('/m') !== 0 &&
        p.indexOf('/static/') !== 0 &&
        p !== '/manifest.webmanifest') return;

    // 移动端资源：网络优先，失败回退到缓存（离线壳可用）
    e.respondWith(
        fetch(req).then(function (res) {
            var copy = res.clone();
            caches.open(CACHE).then(function (c) { c.put(req, copy); });
            return res;
        }).catch(function () { return caches.match(req); })
    );
});
