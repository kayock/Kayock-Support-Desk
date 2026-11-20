/* Kayock Support Desk service worker.
 *
 * Honest offline policy:
 *  - STATIC ASSETS (css/js/vendor/icons) are cached and served offline.
 *  - PAGES are network-first; if the local server is unreachable, the
 *    last cached copy of a page you already visited is shown WITH A
 *    CLEAR "OFFLINE — CACHED PAGE" BANNER injected at the top, and any
 *    AI answer/search panel on it will show its own offline error.
 *  - API CALLS (questions, search, answers, AI control) are NEVER
 *    faked or cached: AI answers require the local model server, and
 *    an offline cached answer is always labeled as such. This worker
 *    never pretends a live AI query succeeded.
 */
const CACHE = "kayock-static-v1";
const STATIC_ASSETS = [
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/global-ai.js",
  "/static/js/theme.js",
  "/static/vendor/pdfjs/pdf.min.js",
  "/static/vendor/pdfjs/pdf.worker.min.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Never touch non-GET requests (answers, uploads, settings, AI start/stop).
  if (req.method !== "GET") return;
  // Never cache API calls -- live data and AI answers only.
  if (url.pathname.startsWith("/api/")) return;

  // Same-origin static assets: cache-first.
  if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((hit) =>
        hit ||
        fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
      )
    );
    return;
  }

  // Navigations: network-first with an honestly-labeled cached fallback.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(async () => {
          const hit = await caches.match(req);
          if (!hit) {
            return new Response(
              "<!DOCTYPE html><html><head><meta charset='utf-8'>" +
              "<meta name='viewport' content='width=device-width, initial-scale=1'>" +
              "<title>Kayock offline</title></head><body style='font-family:sans-serif;padding:40px;max-width:600px;margin:0 auto;'>" +
              "<h2>You are offline</h2><p>This page was not visited before, so there is no cached copy.</p>" +
              "<p>Reconnect to the computer running Kayock to search manuals and ask questions.</p>" +
              "</body></html>",
              { headers: { "Content-Type": "text/html" } }
            );
          }
          // Serve the cached page with a clearly marked offline banner.
          const text = await hit.text();
          const banner =
            "<div style=\"position:sticky;top:0;z-index:999;background:#b3492f;color:#fff;padding:10px 16px;font-size:14px;\">" +
            "&#9888; Offline — cached page. AI answers and manual search need the local Kayock server; " +
            "anything below was generated earlier and is NOT a live result.</div>";
          const marked = /<body[^>]*>/i.test(text)
            ? text.replace(/<body[^>]*>/i, (m) => m + banner)
            : banner + text;
          return new Response(marked, { status: hit.status, headers: hit.headers });
        })
    );
  }
});
