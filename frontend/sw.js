/* Avatar Democracy static shell.
   CACHE_VERSION: bump this string in a release so activate() deletes old caches.
   Caches HTML, CSS, JS, and icons only. Never caches /api or any analysis or vote response.
   Network-first for HTML (and the rest of the shell) so a new release shows while online.
   Service workers run only on https or localhost. */

const CACHE_VERSION = "avatar-democracy-shell-v1";

const SHELL_PATHS = [
  "/",
  "/analyze",
  "/avatar",
  "/vote",
  "/bills",
  "/chamber",
  "/history",
  "/manifest.webmanifest",
  "/static/style.css",
  "/static/app.js",
  "/static/avatar.js",
  "/static/vote.js",
  "/static/bills.js",
  "/static/chamber.js",
  "/static/history.js",
  "/static/pwa.js",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
];

function isApiPath(pathname) {
  return pathname === "/api" || pathname.startsWith("/api/");
}

function isShellPath(pathname) {
  return SHELL_PATHS.indexOf(pathname) !== -1;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => cache.addAll(SHELL_PATHS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  let url;
  try {
    url = new URL(request.url);
  } catch (error) {
    return;
  }
  if (url.origin !== self.location.origin) return;

  // Never cache /api or any analysis or vote response.
  if (isApiPath(url.pathname)) return;

  if (!isShellPath(url.pathname)) return;

  event.respondWith(networkFirst(request));
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE_VERSION);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw error;
  }
}
