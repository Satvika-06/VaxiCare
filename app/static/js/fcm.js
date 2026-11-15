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

const controls = document.getElementById("fcm-controls");
if (controls) {
  const status = document.getElementById("fcm-status");
  const enableButton = document.getElementById("fcm-enable");
  const disableButton = document.getElementById("fcm-disable");
  const testButton = document.getElementById("fcm-test");
  const firebaseConfig = JSON.parse(controls.dataset.firebaseConfig);
  const firebaseApp = initializeApp(firebaseConfig);

  const setStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle("text-danger", isError);
    status.classList.toggle("text-success", !isError && Boolean(message));
  };

  const postInstallationId = async (url, installationId) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": controls.dataset.csrfToken,
      },
      body: JSON.stringify({ fid: installationId }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "The notification preference could not be saved.");
    }
  };

  (async () => {
    try {
      if (!("serviceWorker" in navigator) || !("Notification" in window) || !(await isSupported())) {
        throw new Error("This browser does not support push notifications.");
      }

      const messaging = getMessaging(firebaseApp);
      let registrationWaiter = null;
      let unregistrationRequest = null;

      onRegistered(messaging, async (installationId) => {
        sessionStorage.setItem("vaxicare-fid", installationId);
        try {
          await postInstallationId(controls.dataset.registerUrl, installationId);
          testButton.disabled = false;
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
        testButton.disabled = true;
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
            reject(new Error("Firebase registration did not return an installation ID."));
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

      enableButton.addEventListener("click", async () => {
        enableButton.disabled = true;
        try {
          const permission = await Notification.requestPermission();
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

      onMessage(messaging, (payload) => {
        const data = payload.data || {};
        if (Notification.permission === "granted") {
          new Notification(data.title || "VaxiCare", {
            body: data.body || "A vaccination schedule update is available.",
          });
        }
      });

      if (Notification.permission === "granted") {
        await registerBrowser();
      }
    } catch (error) {
      setStatus(error.message || "Push notifications are unavailable.", true);
      enableButton.disabled = true;
      disableButton.disabled = true;
    }
  })();
}
