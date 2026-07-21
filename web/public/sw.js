/**
 * GameGusto service worker.
 *
 * Purpose: an installed app that opens without a connection should show its
 * own shell, not a browser error page. Nothing more — this is not an offline
 * mode. The library and the agent both need the network, and pretending
 * otherwise by serving stale data would be worse than an honest message.
 *
 * Rules, in order of how much they matter:
 *
 *   1. /api/* is never intercepted. Not "not cached" — not intercepted at
 *      all. The chat endpoint is a token-by-token SSE stream, and passing a
 *      streaming body back through a service worker is the kind of thing
 *      that works locally and buffers in production. Leaving those requests
 *      to the browser removes the risk entirely.
 *
 *   2. Navigations are network-first. index.html names the current hashed
 *      bundles, so serving it from cache after a deploy would pin the app to
 *      a stale build. Cache is the fallback, not the default — and only a
 *      genuinely OK response is ever written back, or a transient 5xx would
 *      poison the cache and the app would "sometimes" open broken.
 *
 *   3. The bundles index.html names are precached with it, in the same step.
 *      Caching them lazily on first fetch looks like it works and does not:
 *      the shell and its scripts then live or die separately, so a deploy
 *      followed by going offline leaves a cached page pointing at scripts
 *      that were never stored — a blank screen, which is worse than an
 *      error page. Shell and bundles are cached together or not at all.
 */

const VERSION = "v2";
const SHELL_CACHE = `gg-shell-${VERSION}`;

/** Unhashed files that every build needs. */
const STATIC_SHELL = [
  "/index.html",
  "/manifest.webmanifest",
  "/icon.svg",
  "/apple-touch-icon.png",
  "/icon-192.png",
  "/fonts/press-start-2p.woff2",
  "/fonts/share-tech-mono.woff2",
];

/**
 * Cache index.html together with every bundle it references.
 *
 * The asset names carry content hashes and change every build, so the list
 * is read out of the HTML rather than hard-coded or generated at build time
 * — that keeps the worker correct without coupling it to the bundler.
 */
async function cacheShellAndBundles(response) {
  const cache = await caches.open(SHELL_CACHE);
  const html = await response.clone().text();

  const bundles = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);

  await cache.put("/index.html", response.clone());
  await Promise.allSettled(bundles.map((url) => cache.add(url)));
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // Individually and tolerantly: one 404 inside addAll rejects the whole
      // install and leaves the app with no worker at all.
      await Promise.allSettled(STATIC_SHELL.map((url) => cache.add(url)));

      const index = await fetch("/index.html", { cache: "reload" }).catch(() => null);
      if (index?.ok) await cacheShellAndBundles(index);

      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n !== SHELL_CACHE).map((n) => caches.delete(n)));
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Rule 1. Note the absence of respondWith: this hands the request back to
  // the browser untouched, which is what keeps the SSE stream intact.
  if (url.pathname.startsWith("/api/")) return;

  // Same-origin GETs only. Cognito's token endpoint is a cross-origin POST
  // and must never be touched.
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  // Rule 2 + 3.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          if (response.ok) {
            // waitUntil, not fire-and-forget: without it the write is
            // abandoned when the page navigates away, which is exactly how
            // the bundles silently failed to cache.
            event.waitUntil(cacheShellAndBundles(response));
          }
          return response;
        } catch {
          return (await caches.match("/index.html")) ?? offlineFallback();
        }
      })(),
    );
    return;
  }

  event.respondWith(
    (async () => {
      const hit = await caches.match(request);
      if (hit) return hit;
      try {
        return await fetch(request);
      } catch {
        return Response.error();
      }
    })(),
  );
});

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
