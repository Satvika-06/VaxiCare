// ==========================================================================
// VaxiCare – Official Firebase & Web Push Background Service Worker
// Handles background push events, system notification banners, and click deep-linking
// ==========================================================================

self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", function (event) {
  if (!event.data) {
    return;
  }

  let title = "VaxiCare Immunization Reminder";
  let body = "A scheduled vaccination milestone is due for your child.";
  let deepLink = "/parent-dashboard";
  let tag = "vaxicare-reminder";

  try {
    const json = event.data.json();
    if (json.notification) {
      title = json.notification.title || title;
      body = json.notification.body || body;
    } else if (json.data) {
      title = json.data.title || title;
      body = json.data.body || body;
    }
    if (json.data) {
      deepLink = json.data.url || json.data.click_action || deepLink;
      tag = json.data.tag || tag;
    }
  } catch (err) {
    body = event.data.text() || body;
  }

  const notificationOptions = {
    body: body,
    icon: "/static/img/vaxicare-icon.png",
    badge: "/static/img/vaxicare-badge.png",
    tag: tag,
    renotify: true,
    requireInteraction: true,
    data: {
      url: deepLink,
      timestamp: Date.now()
    }
  };

  event.waitUntil(
    self.registration.showNotification(title, notificationOptions)
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : "/parent-dashboard";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientList) {
      for (let i = 0; i < clientList.length; i++) {
        const client = clientList[i];
        if ("focus" in client && client.url.includes(targetUrl)) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
