/* Delhi Traffic Copilot service worker: offline app shell + web push. */
const CACHE = "dtc-shell-v1";
const SHELL = ["/", "/advisories", "/routes", "/about", "/manifest.json", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(req).then((r) => r || caches.match("/"))));
  }
});

self.addEventListener("push", (event) => {
  let data = { title: "Delhi Traffic Copilot", body: "New traffic update on your route", url: "/routes" };
  try {
    data = { ...data, ...event.data.json() };
  } catch (e) {
    /* plain text */
  }
  event.waitUntil(self.registration.showNotification(data.title, { body: data.body, icon: "/icon.svg", tag: data.tag, data: { url: data.url } }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/routes";
  event.waitUntil(self.clients.openWindow(url));
});
