// Sportfinder – Service Worker: App startet schnell und funktioniert auch bei schwachem Netz.
const SHELL = "sf-shell-v1";
const DATA = "sf-data-v1";
const ASSETS = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // App selbst: immer zuerst frisch aus dem Netz, sonst aus dem Speicher
  if (req.mode === "navigate" || (url.origin === location.origin && url.pathname.endsWith("index.html"))) {
    e.respondWith(fetch(req).then(r => {
      const copy = r.clone(); caches.open(SHELL).then(c => c.put("index.html", copy)); return r;
    }).catch(() => caches.match("index.html")));
    return;
  }

  // Daten-Kacheln: sofort aus dem Speicher, im Hintergrund aktualisieren
  if (url.origin === location.origin && url.pathname.includes("/data/")) {
    e.respondWith(caches.open(DATA).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }

  // Schriften, Karten-Bibliothek, Icons: einmal laden, dann aus dem Speicher
  if (url.hostname.endsWith("gstatic.com") || url.hostname.endsWith("googleapis.com") ||
      url.hostname === "cdnjs.cloudflare.com" || (url.origin === location.origin && /\.(png|webmanifest)$/.test(url.pathname))) {
    e.respondWith(caches.open(SHELL).then(async c => {
      const hit = await c.match(req);
      return hit || fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; });
    }));
  }
});
