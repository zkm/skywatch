"use strict";
/* Service worker: only shows push notifications. No offline caching. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d;
  try { d = e.data.json(); } catch { d = { title: "Skywatch", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Skywatch", {
    body: d.body || "",
    tag: d.id ? `msg-${d.id}` : "skywatch",
    icon: "icon-192.png",
    badge: "badge-96.png",
    data: { url: d.url || "./" },
  }));
});

// open (or focus) Skywatch when a notification is tapped
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL(e.notification.data.url || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const open = wins.find((w) => w.url.startsWith(self.registration.scope));
    return open ? open.focus() : self.clients.openWindow(url);
  }));
});
