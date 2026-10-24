"use strict";
/* "Notify me": browser push for new EAMs via sw.js. Hidden when the server has push off. */
const notify = { reg: null, key: null, busy: false };
const notifyBtn = $("notify");

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 7000);
}

function renderNotify(on) {
  notifyBtn.setAttribute("aria-pressed", String(on));
  $("notify-label").textContent = on ? "Notifications on" : "Notify me";
  notifyBtn.title = on ? "Stop notifications for new EAMs" : "Get a notification for each new EAM";
}

const b64ToBytes = (s) => {
  const raw = atob((s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};
const sameKey = (buf, key) => {
  if (!buf) return false;
  const a = new Uint8Array(buf), b = b64ToBytes(key);
  return a.length === b.length && a.every((v, i) => v === b[i]);
};
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

const postJSON = (url, body) => fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
}).then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status); });

async function notifyInit() {
  if (DEMO) return;                              // no server to send pushes
  try { notify.key = (await getJSON("api/push/key")).key; } catch { return; }   // push off on this server
  notifyBtn.hidden = false;
  if (!pushSupported()) { renderNotify(false); return; }
  notify.reg = await navigator.serviceWorker.register("sw.js");
  let sub = await notify.reg.pushManager.getSubscription();
  // the server's key changed (fresh data dir): the old subscription can't receive anything
  if (sub && !sameKey(sub.options.applicationServerKey, notify.key)) { await sub.unsubscribe(); sub = null; }
  const on = !!sub && Notification.permission === "granted";
  renderNotify(on);
  if (on) postJSON("api/push/subscribe", sub.toJSON()).catch(() => {});   // re-sync; no-op if known
}

async function notifyToggle() {
  if (notify.busy) return;
  if (!pushSupported()) {
    toast(isIOS() && !navigator.standalone
      ? "On iPhone and iPad, add Skywatch to your Home Screen first (Share → Add to Home Screen), then turn notifications on from there."
      : "This browser doesn't support push notifications.");
    return;
  }
  notify.busy = true;
  try {
    const existing = await notify.reg.pushManager.getSubscription();
    if (existing && notifyBtn.getAttribute("aria-pressed") === "true") {
      await postJSON("api/push/unsubscribe", { endpoint: existing.endpoint }).catch(() => {});
      await existing.unsubscribe();
      renderNotify(false);
      toast("Notifications off.");
      return;
    }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") {
      toast(perm === "denied"
        ? "Notifications are blocked for this site. Allow them in your browser's site settings, then try again."
        : "Notifications weren't turned on.");
      return;
    }
    const sub = existing || await notify.reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: b64ToBytes(notify.key),
    });
    await postJSON("api/push/subscribe", sub.toJSON());
    renderNotify(true);
    toast("Notifications on. You'll get one for each new EAM; a test is on its way.");
  } catch (err) {
    toast(`Couldn't turn on notifications: ${err.message}`);
  } finally {
    notify.busy = false;
  }
}

notifyBtn.addEventListener("click", notifyToggle);
notifyInit().catch(() => {});
