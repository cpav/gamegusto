/**
 * GameGusto service worker.
 *
 * Purpose: an installed app that opens without a connection should show its
 * own shell, not a browser error page. Nothing more — this is not an offline
 * mode. The library and the agent both need the network, and pretending
 * otherwise by serving stale data would be worse than an honest message.
 *
 * Three rules, in order of how much they matter:
 *
 *   1. /api/* is never intercepted. Not "not cached" — not intercepted at
 *      all. The chat endpoint is a token-by-token SSE stream, and passing a
 *      streaming body back through a service worker is the kind of thing
 *      that works locally and buffers in production. Leaving those requests
 *      to the browser removes the risk entirely.
 *
 *   2. Navigations are network-first. index.html names the current hashed
 *      bundles, so serving it from cache after a deploy would pin the app to
 *      a stale build — with the old JS already evicted. Cache is the
 *      fallback, not the default.
 *
 *   3. Hashed assets are cache-first, because /assets/index-A1B2C3.js is
 *      immutable by construction: a new build produces a new name.
 */

const VERSION = "v1";
const SHELL_CACHE = `gg-shell-${VERSION}`;
const ASSET_CACHE = `gg-assets-${VERSION}`;

/** Enough to render the shell offline. Unhashed, so listed explicitly. */
const SHELL = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/icon.svg",
  "/apple-touch-icon.png",
  "/icon-192.png",
  "/fonts/press-start-2p.woff2",
  "/fonts/share-tech-mono.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      // Individually, and tolerant of failures: one 404 in addAll rejects the
      // whole install and leaves the app with no worker at all.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name !== SHELL_CACHE && name !== ASSET_CACHE)
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Rule 1. Note the absence of respondWith: this hands the request back to
  // the browser untouched, which is what keeps the SSE stream intact.
  if (url.pathname.startsWith("/api/")) return;

  // Only our own origin, and only GET. Cognito's token endpoint is a POST to
  // another origin and must never be touched.
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  // Rule 2.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put("/index.html", copy));
          return response;
        })
        .catch(() => caches.match("/index.html").then((hit) => hit || offlineFallback())),
    );
    return;
  }

  // Rule 3, plus the shell's own unhashed files.
  event.respondWith(
    caches.match(request).then((hit) => {
      if (hit) return hit;
      return fetch(request)
        .then((response) => {
          if (response.ok && (url.pathname.startsWith("/assets/") || isShellAsset(url.pathname))) {
            const copy = response.clone();
            caches.open(ASSET_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => hit);
    }),
  );
});

function isShellAsset(pathname) {
  return (
    pathname.startsWith("/fonts/") || pathname.endsWith(".png") || pathname.endsWith(".svg")
  );
}

/** Last resort: the cache was evicted and there is no network. */
function offlineFallback() {
  return new Response(
    `<!doctype html><meta charset="utf-8">
     <meta name="viewport" content="width=device-width,initial-scale=1">
     <title>GameGusto</title>
     <body style="margin:0;display:grid;place-items:center;height:100vh;
                  background:#0e101c;color:#a7adc4;
                  font:15px/1.5 -apple-system,system-ui,sans-serif;text-align:center">
       <div><p style="color:#f0efe9">You're offline.</p>
       <p>GameGusto needs a connection to pick something for tonight.</p></div>
     </body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" }, status: 200 },
  );
}
