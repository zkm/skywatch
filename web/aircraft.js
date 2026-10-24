"use strict";
/* Strategic aircraft map. Leaflet loads on first visit to the tab. */
const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/";
const air = { map: null, layers: new Map(), snap: null, loading: null, fitted: false };

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  air.loading ||= new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = LEAFLET + "leaflet.min.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = LEAFLET + "leaflet.min.js";
    js.onload = resolve;
    js.onerror = () => { air.loading = null; reject(new Error("map library failed to load")); };
    document.head.appendChild(js);
  });
  return air.loading;
}

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const short = (label) => label.split(" ")[0];
const altText = (a) => (a.alt === "ground" ? "on ground" : a.alt != null ? `${Math.round(a.alt).toLocaleString()} ft` : "—");
function ago(epoch) {
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function airMeta(snap) {
  if (DEMO) return snap.updated_at ? `Snapshot taken ${zTime(snap.updated_at)}, when this demo was built.` : "No snapshot in this build.";
  if (snap.error && !snap.updated_at) return `ADS-B feed unavailable: ${snap.error}`;
  const when = snap.updated_at ? `Updated ${zTime(snap.updated_at, false)}` : "Waiting for first update…";
  let feed = "ADS-B feed";
  try { feed = new URL(snap.source).host || "local file"; } catch { /* keep generic */ }
  return `${when} · public ADS-B via ${feed}${snap.error ? ` · last poll failed: ${snap.error}` : ""}`;
}

function renderAirTable(snap) {
  $("air-meta").textContent = airMeta(snap);
  const list = [...snap.aircraft].sort((a, b) => b.seen_at - a.seen_at);
  $("air-rows").innerHTML = list.length
    ? list.map((a) => `<tr data-hex="${esc(a.hex)}" tabindex="0">
        <td>${esc(a.label)}</td><td class="code">${esc(a.callsign || "—")}</td><td>${esc(a.reg || "—")}</td>
        <td class="num">${altText(a)}</td><td class="num">${a.gs != null ? `${Math.round(a.gs)} kt` : "—"}</td>
        <td class="num">${a.lat == null ? "no position · " : ""}${ago(a.seen_at)}</td></tr>`).join("")
    : `<tr class="empty-row"><td colspan="6">${DEMO
      ? "None of the watched aircraft were being heard when this snapshot was taken. A running Skywatch server updates this every 30 seconds."
      : "None of the watched aircraft are being heard right now. They often fly without broadcasting, so check back later."}</td></tr>`;
}

function renderMap(snap) {
  if (!air.map) return;
  const ink = cssVar("--ink"), signal = cssVar("--signal");
  const seen = new Set();
  for (const a of snap.aircraft) {
    if (a.lat == null || a.lon == null) continue;
    seen.add(a.hex);
    const icon = L.divIcon({
      className: "ac",
      iconSize: [28, 28], iconAnchor: [14, 14],
      html: `<svg viewBox="0 0 24 24" width="28" height="28" style="transform:rotate(${a.track || 0}deg)" aria-hidden="true">
        <path d="M12 2 L14 10 L22 14 L22 16 L14 14 L13.5 19 L16 21 L16 22 L12 21 L8 22 L8 21 L10.5 19 L10 14 L2 16 L2 14 L10 10 Z" fill="${ink}" stroke="${signal}" stroke-width="1"/></svg>`,
    });
    let l = air.layers.get(a.hex);
    if (!l) {
      l = {
        marker: L.marker([a.lat, a.lon], { icon, keyboard: false }).addTo(air.map),
        trail: L.polyline([], { color: signal, weight: 2, opacity: 0.8 }).addTo(air.map),
      };
      air.layers.set(a.hex, l);
    }
    l.marker.setLatLng([a.lat, a.lon]).setIcon(icon);
    l.marker.unbindTooltip().bindTooltip(esc(short(a.label)), { permanent: true, direction: "right", offset: [12, 0], className: "ac-tip" });
    l.marker.bindPopup(`<strong>${esc(a.label)}</strong><br>${esc(a.callsign || "no callsign")} · ${esc(a.reg || "tail unknown")}<br>
      ${altText(a)}${a.gs != null ? ` · ${Math.round(a.gs)} kt` : ""}${a.squawk ? ` · squawk ${esc(a.squawk)}` : ""}<br>Heard ${ago(a.seen_at)}`);
    l.trail.setLatLngs(a.trail || []);
  }
  for (const [hex, l] of air.layers) {
    if (seen.has(hex)) continue;
    l.marker.remove(); l.trail.remove(); air.layers.delete(hex);
  }
  if (!air.fitted && air.layers.size) {
    air.map.fitBounds(L.latLngBounds([...air.layers.values()].map((l) => l.marker.getLatLng())).pad(0.5), { maxZoom: 6 });
    air.fitted = true;
  }
}

function renderAir(snap) {
  air.snap = snap;
  renderAirTable(snap);
  renderMap(snap);
}

async function airOpen() {
  if (!air.snap) {
    try { renderAir(await getJSON(api.aircraft())); }
    catch (e) { $("air-meta").textContent = `Couldn't load aircraft: ${e.message}`; }
  }
  try { await loadLeaflet(); }
  catch (e) { $("map").textContent = `The map couldn't load (${e.message}). The list below still works.`; return; }
  if (!air.map) {
    air.map = L.map("map", { worldCopyJump: true, zoomControl: true }).setView([39, -97], 4);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 12,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · positions: <a href="https://adsb.lol">adsb.lol</a>',
    }).addTo(air.map);
  }
  air.map.invalidateSize();     // the tab was hidden when the container was laid out
  if (air.snap) renderMap(air.snap);
}

$("air-rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-hex]");
  const l = tr && air.layers.get(tr.dataset.hex);
  if (l && air.map) { air.map.setView(l.marker.getLatLng(), Math.max(air.map.getZoom(), 6)); l.marker.openPopup(); }
});
$("air-rows").addEventListener("keydown", (e) => {
  if (e.key === "Enter") e.target.closest("tr[data-hex]")?.click();
});

hooks.aircraft = (snap) => renderAir(snap);
const prevAirView = hooks.view;
hooks.view = (name) => {
  prevAirView?.(name);
  if (name === "aircraft") airOpen();
};
