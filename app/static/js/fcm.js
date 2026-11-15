import { initializeApp } from "https://www.gstatic.com/firebasejs/13.3.0/firebase-app.js";
import {
  getMessaging,
  isSupported,
  onMessage,
  onRegistered,
  onUnregistered,
  register,
  unregister,
} from "https://www.gstatic.com/firebasejs/13.3.0/firebase-messaging.js";

function getDeviceName() {
  const ua = navigator.userAgent;
  let os = "Desktop";
  if (/Android/i.test(ua)) os = "Android";
  else if (/iPhone|iPad|iPod/i.test(ua)) os = "iOS";
  else if (/Windows/i.test(ua)) os = "Windows";
  else if (/Macintosh|Mac OS X/i.test(ua)) os = "macOS";
  else if (/Linux/i.test(ua)) os = "Linux";

  let browser = "Browser";
  if (/Edg/i.test(ua)) browser = "Edge";
  else if (/Chrome/i.test(ua)) browser = "Chrome";
  else if (/Firefox/i.test(ua)) browser = "Firefox";
  else if (/Safari/i.test(ua)) browser = "Safari";

  return `${browser} on ${os}`;
}

const controls = document.getElementById("fcm-controls");
if (controls) {
  const status = document.getElementById("fcm-status");
  const enableButton = document.getElementById("fcm-enable");
  const disableButton = document.getElementById("fcm-disable");
  const testButton = document.getElementById("fcm-test");
  const permissionBadge = document.getElementById("push-permission-badge");
  const firebaseConfig = JSON.parse(controls.dataset.firebaseConfig || "{}");
  
  let firebaseApp = null;
  if (firebaseConfig && firebaseConfig.apiKey && firebaseConfig.projectId) {
    try {
      firebaseApp = initializeApp(firebaseConfig);
    } catch (e) {
      console.warn("Firebase initializeApp error:", e);
    }
  }

  const updatePermissionBadge = () => {
    if (!permissionBadge) return;
    if (!("Notification" in window)) {
      permissionBadge.className = "badge bg-danger";
      permissionBadge.textContent = "Unsupported";
      return;
    }
    if (Notification.permission === "granted") {
      permissionBadge.className = "badge bg-success";
      permissionBadge.textContent = "Granted";
    } else if (Notification.permission === "denied") {
      permissionBadge.className = "badge bg-danger";
      permissionBadge.textContent = "Blocked / Denied";
    } else {
      permissionBadge.className = "badge bg-warning text-dark";
      permissionBadge.textContent = "Default (Not requested)";
    }
  };

  const setStatus = (message, isError = false) => {
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("text-danger", isError);
    status.classList.toggle("text-success", !isError && Boolean(message));
    updatePermissionBadge();
  };

  updatePermissionBadge();

  const postInstallationId = async (url, token) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": controls.dataset.csrfToken,
      },
      body: JSON.stringify({
        token: token,
        fid: token,
        device_name: getDeviceName(),
        permission_status: ("Notification" in window) ? Notification.permission : "unknown",
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || result.message || "The notification preference could not be saved.");
    }
    return result;
  };

  (async () => {
    try {
      if (!("serviceWorker" in navigator) || !("Notification" in window)) {
        throw new Error("This browser does not support push notifications.");
      }

      const supported = await isSupported().catch(() => false);
      if (!supported || !firebaseApp) {
        if (!firebaseApp) {
          setStatus("Firebase web configuration is pending in project environment.", false);
        } else {
          setStatus("Push messaging is not supported in this browser context.", true);
        }
        return;
      }

      const messaging = getMessaging(firebaseApp);
      let registrationWaiter = null;
      let unregistrationRequest = null;

      onRegistered(messaging, async (installationId) => {
        sessionStorage.setItem("vaxicare-fid", installationId);
        try {
          await postInstallationId(controls.dataset.registerUrl, installationId);
          if (testButton) testButton.disabled = false;
          setStatus("Push notifications are enabled on this browser.");
          if (registrationWaiter) {
            registrationWaiter.resolve();
            registrationWaiter = null;
          }
        } catch (error) {
          setStatus(error.message || "This browser could not be registered.", true);
          if (registrationWaiter) {
            registrationWaiter.reject(error);
            registrationWaiter = null;
          }
        }
      });

      onUnregistered(messaging, (installationId) => {
        sessionStorage.removeItem("vaxicare-fid");
        if (testButton) testButton.disabled = true;
        unregistrationRequest = postInstallationId(
          controls.dataset.disableUrl,
          installationId,
        ).catch((error) => {
          setStatus(error.message || "This browser could not be disabled.", true);
          return false;
        });
      });

      const ensureServiceWorker = async () => navigator.serviceWorker.register(
        "/firebase-messaging-sw.js",
        { scope: "/" },
      );

      const registerBrowser = async () => {
        const serviceWorkerRegistration = await ensureServiceWorker();
        await new Promise((resolve, reject) => {
          const timeout = window.setTimeout(() => {
            registrationWaiter = null;
            reject(new Error("Firebase registration did not return a push token within 15 seconds."));
          }, 15000);
          registrationWaiter = {
            resolve: () => {
              window.clearTimeout(timeout);
              resolve();
            },
            reject: (error) => {
              window.clearTimeout(timeout);
              reject(error);
            },
          };
          register(messaging, {
            vapidKey: controls.dataset.vapidKey,
            serviceWorkerRegistration,
          }).catch((error) => {
            if (registrationWaiter) {
              registrationWaiter.reject(error);
              registrationWaiter = null;
            }
          });
        });
      };

      if (enableButton) {
        enableButton.addEventListener("click", async () => {
          enableButton.disabled = true;
          try {
            const permission = await Notification.requestPermission();
            updatePermissionBadge();
            if (permission !== "granted") {
              setStatus("Browser notification permission was not granted.", true);
              return;
            }
            await registerBrowser();
          } catch (error) {
            setStatus(error.message || "Push notifications could not be enabled.", true);
          } finally {
            enableButton.disabled = false;
          }
        });
      }

      if (disableButton) {
        disableButton.addEventListener("click", async () => {
          disableButton.disabled = true;
          try {
            const installationId = sessionStorage.getItem("vaxicare-fid");
            if (installationId) {
              await postInstallationId(controls.dataset.disableUrl, installationId);
            }
            await unregister(messaging);
            if (unregistrationRequest) {
              const disabled = await unregistrationRequest;
              if (disabled === false) {
                throw new Error("This browser could not be disabled on the server.");
              }
            }
            sessionStorage.removeItem("vaxicare-fid");
            setStatus("Push notifications are disabled on this browser.");
          } catch (error) {
            setStatus(error.message || "Push notifications could not be disabled.", true);
          } finally {
            disableButton.disabled = false;
          }
        });
      }

      if (testButton) {
        testButton.addEventListener("click", async () => {
          const installationId = sessionStorage.getItem("vaxicare-fid");
          if (!installationId || !window.confirm("Send one test notification to this browser only?")) {
            return;
          }
          testButton.disabled = true;
          try {
            await postInstallationId(controls.dataset.testUrl, installationId);
            setStatus("Firebase accepted the test notification for this browser.");
          } catch (error) {
            setStatus(error.message || "The test notification could not be sent.", true);
          } finally {
            testButton.disabled = !sessionStorage.getItem("vaxicare-fid");
          }
        });
      }

      onMessage(messaging, (payload) => {
        const data = payload.notification || payload.data || {};
        const title = data.title || "VaxiCare Reminder";
        const body = data.body || "A vaccination schedule update is available.";
        if (Notification.permission === "granted") {
          try {
            new Notification(title, {
              body: body,
              icon: "/static/img/icon-192.png",
              badge: "/static/img/vaxicare-badge.png",
            });
          } catch (e) {
            console.warn("Foreground notification error:", e);
          }
        }
      });

      // If user previously granted permission, quietly refresh token registration
      if (Notification.permission === "granted") {
        await registerBrowser().catch(() => {});
      }
    } catch (error) {
      setStatus(error.message || "Push notifications are unavailable.", true);
      if (enableButton) enableButton.disabled = true;
      if (disableButton) disableButton.disabled = true;
    }
  })();
}
