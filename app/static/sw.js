// Dukaan service worker: keeps the app itself on the device so billing works with no connection.
// Bills and items are kept by the app (localStorage); this only caches the app's own files.
const CACHE = "dukaan-__VERSION__";
const SHELL = ["/", "/app.js?v=__VERSION__", "/gst.js?v=__VERSION__", "/app.css?v=__VERSION__", "/icon.svg", "/icon-192.png", "/manifest.webmanifest"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/b/")) return;
  if (e.request.mode === "navigate") {
    // the newest app when online, the saved one when not
    e.respondWith(fetch(e.request).then((r) => { const c = r.clone(); caches.open(CACHE).then((x) => x.put("/", c)); return r; })
      .catch(() => caches.match("/")));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
    if (r.ok) { const c = r.clone(); caches.open(CACHE).then((x) => x.put(e.request, c)); }
    return r;
  })));
});
