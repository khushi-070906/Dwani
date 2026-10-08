/* DwaniForms service worker: makes the app open instantly and survive a dropped connection.

   What it keeps: the page itself, the bundled fonts and icons, and the three public lists (services, capabilities,
   screen text). NOTHING from /sessions is ever read, cached or even intercepted -- that is where a citizen's answers
   live, and they stay on the server. Bump CACHE to roll out a new page. */
const CACHE = "dwaniforms-v1";
const SHELL = [
  "./",
  "./manifest.webmanifest",
  "./assets/img/icon-192.png",
  "./assets/img/icon-512.png",
  "./assets/img/icon-maskable-512.png",
  "./assets/fonts/RozhaOne-Regular.ttf",
  "./assets/fonts/Mukta-Regular.ttf",
  "./assets/fonts/Mukta-SemiBold.ttf"
];

self.addEventListener("install", (e) => {
  // One missing file must not stop the whole install, so each is added on its own.
  e.waitUntil(caches.open(CACHE)
    .then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => {}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function cacheable(url) {
  if (url.pathname.indexOf("/sessions") > -1) return false;          // a citizen's answers: never ours to keep
  return /\/(assets\/|manifest\.webmanifest$|forms$|capabilities$|ui\/[a-z]{2,3}$)/.test(url.pathname) ||
         url.pathname.endsWith("/form/") || url.pathname.endsWith("/form");
}

async function keep(req, res) {
  if (res && res.ok && res.type === "basic") {
    const c = await caches.open(CACHE);
    c.put(req, res.clone()).catch(() => {});
  }
  return res;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== self.location.origin || !cacheable(url)) return;   // straight to the network, untouched

  if (req.mode === "navigate") {
    // Fresh page when there is a network, the stored one when there is not.
    e.respondWith(fetch(req).then((r) => keep(req, r)).catch(() => caches.match("./").then((r) => r || Response.error())));
    return;
  }
  const fresh = /\/(forms|capabilities|ui\/[a-z]{2,3})$/.test(url.pathname);
  if (fresh) {
    e.respondWith(fetch(req).then((r) => keep(req, r)).catch(() => caches.match(req).then((r) => r || Response.error())));
  } else {
    e.respondWith(caches.match(req).then((r) => r || fetch(req).then((x) => keep(req, x))));
  }
});
