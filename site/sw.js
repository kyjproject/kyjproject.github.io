// Offline support for KYJ-SAT, opt-in via the Lab tab's "Offline mode"
// toggle (see registerOfflineServiceWorker() in index.html). Registered
// unconditionally once that toggle is on — harmless with an empty cache,
// since nothing here does anything until either the browser visits a URL
// (image caching, on any normal visit) or the Lab "Download for offline"
// button explicitly bulk-fetches every question image.
//
// Two different caching strategies, deliberately:
//   - Images (IMAGE_CACHE): cache-first. Question images live at a fixed
//     path forever once published, so there's no staleness risk, and
//     cache-first is what makes them actually work offline.
//   - The app shell (SHELL_CACHE: this page + data.js): network-first,
//     falling back to cache only when the network fetch itself fails. This
//     is a frequently-updated site — a naive cache-first here would mean a
//     fresh deploy could be invisibly masked by a stale cached copy for
//     anyone who'd ever gone offline once. Network-first keeps normal
//     (online) browsing always fresh, while still letting the app load with
//     no connection once it's been visited at least once.
const SHELL_CACHE = 'kyj-sat-shell-v1';
const IMAGE_CACHE = 'kyj-sat-images-v1'; // must match OFFLINE_IMAGE_CACHE in index.html

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL_CACHE && k !== IMAGE_CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

function isImagePath(pathname) {
  return pathname.includes('/images/') || /\.(png|svg|jpe?g|gif|webp)$/i.test(pathname);
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;

  if (isImagePath(url.pathname)) {
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(IMAGE_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => cached))
    );
    return;
  }

  const isShellFile = url.pathname.endsWith('/')
    || url.pathname.endsWith('/index.html')
    || url.pathname.endsWith('/data.js');
  if (isShellFile) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req))
    );
  }
});
