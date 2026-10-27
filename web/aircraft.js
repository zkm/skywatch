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

// where to draw an aircraft: its live position, else the feed's last-known or rough one
const ROUGH_RADIUS_M = 250000;    // rough = a receiver that hears it, so a few hundred km
function where(a) {
  if (a.lat != null && a.lon != null) return { kind: "live", lat: a.lat, lon: a.lon };
  return a.approx || null;
}
function posLabel(a) {
  const w = where(a);
  if (!w) return "no position";
  if (w.kind === "last") return `position ${ago(w.at).replace(" ago", " old")}`;
  if (w.kind === "rough") return "rough area";
  return "";
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
    ? list.map((a) => {
      const pos = posLabel(a);
      const alt = a.alt != null ? altText(a) : "", spd = a.gs != null ? `${Math.round(a.gs)} kt` : "";
      // desktop: one cell per column. Phone: the two .ac-phone cells instead, as a two-line list
      // ("B-52H … on ground" over "61-0031 · rough area … just now"); CSS shows one set or the other
      const line = (...parts) => parts.filter(Boolean).map(esc).join(" · ");
      return `<tr data-hex="${esc(a.hex)}"${where(a) ? ' class="on-map"' : ""}>
        <td class="ac-name">${!where(a) ? esc(a.label)    // nothing to show on the map
          : `<button type="button" class="rowbtn" aria-label="${esc(a.label)}, show on map">${esc(a.label)}</button>`}</td>
        <td class="code ac-desk">${esc(a.callsign || "—")}</td><td class="ac-desk">${esc(a.reg || "—")}</td>
        <td class="num ac-desk">${esc(alt || "—")}</td><td class="num ac-desk">${esc(spd || "—")}</td>
        <td class="num ac-phone ac-sum">${line(alt, spd)}</td>
        <td class="ac-phone ac-sub">${line(a.reg, a.callsign, pos)}</td>
        <td class="num ac-heard">${pos ? `<span class="ac-desk">${esc(pos)} · </span>` : ""}${ago(a.seen_at)}</td></tr>`;
    }).join("")
    : `<tr class="empty-row"><td colspan="6">${DEMO
      ? "None of the watched aircraft were being heard when this snapshot was taken. A running Skywatch server updates this every 30 seconds."
      : "None of the watched aircraft are being heard right now. They often fly without broadcasting, so check back later."}</td></tr>`;
}

function renderMap(snap) {
  if (!air.map) return;
  const ink = cssVar("--ink"), signal = cssVar("--signal"), soft = cssVar("--ink-soft");
  const seen = new Set();
  for (const a of snap.aircraft) {
    const w = where(a);
    if (!w) continue;
    seen.add(a.hex);
    let l = air.layers.get(a.hex);
    if (l && l.kind !== w.kind) { l.mark.remove(); l.trail.remove(); l = null; }   // e.g. stopped broadcasting
    if (!l) {
      const mark = w.kind === "rough"
        ? L.circle([w.lat, w.lon], { radius: ROUGH_RADIUS_M, color: soft, weight: 1.5, dashArray: "4 4", fillOpacity: 0.08 })
        : L.marker([w.lat, w.lon], { keyboard: false });
      l = { kind: w.kind, mark: mark.addTo(air.map),
            trail: L.polyline([], { color: signal, weight: 2, opacity: 0.8 }).addTo(air.map) };
      air.layers.set(a.hex, l);
    }
    l.mark.setLatLng([w.lat, w.lon]);
    if (w.kind !== "rough") {
      l.mark.setIcon(L.divIcon({
        className: w.kind === "last" ? "ac ac-stale" : "ac",
        iconSize: [28, 28], iconAnchor: [14, 14],
        html: `<svg viewBox="0 0 24 24" width="28" height="28" style="transform:rotate(${a.track || 0}deg)" aria-hidden="true">
          <path d="M12 2 L14 10 L22 14 L22 16 L14 14 L13.5 19 L16 21 L16 22 L12 21 L8 22 L8 21 L10.5 19 L10 14 L2 16 L2 14 L10 10 Z" fill="${ink}" stroke="${signal}" stroke-width="1"/></svg>`,
      }));
    }
    const tip = w.kind === "last" ? ` · ${ago(w.at).replace(" ago", "")}` : "";
    l.mark.unbindTooltip().bindTooltip(esc(short(a.label) + tip), w.kind === "rough"
      ? { permanent: true, direction: "center", className: "ac-tip" }
      : { permanent: true, direction: "right", offset: [12, 0], className: "ac-tip" });
    const note = w.kind === "last" ? `<br><em>Not broadcasting a position now. Shown where it last did, ${ago(w.at)}.</em>`
      : w.kind === "rough" ? "<br><em>Not broadcasting a position. The circle is a rough area around a receiver that hears it.</em>" : "";
    l.mark.bindPopup(`<strong>${esc(a.label)}</strong><br>${esc(a.callsign || "no callsign")} · ${esc(a.reg || "tail unknown")}<br>
      ${altText(a)}${a.gs != null ? ` · ${Math.round(a.gs)} kt` : ""}${a.squawk ? ` · squawk ${esc(a.squawk)}` : ""}<br>Heard ${ago(a.seen_at)}${note}`,
      { maxWidth: 230, autoPanPaddingTopLeft: [52, 12] });   // keep it clear of the zoom buttons
    l.trail.setLatLngs(a.trail || []);
  }
  for (const [hex, l] of air.layers) {
    if (seen.has(hex)) continue;
    l.mark.remove(); l.trail.remove(); air.layers.delete(hex);
  }
  if (!air.fitted && air.layers.size) {
    air.map.fitBounds(L.latLngBounds([...air.layers.values()].map((l) => l.mark.getLatLng())).pad(0.5), { maxZoom: 6 });
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
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · positions: <a href="https://adsb.lol">adsb.lol</a>, <a href="https://adsb.fi">adsb.fi</a>',
    }).addTo(air.map);
    // attribution links (Leaflet, OpenStreetMap, the ADS-B feeds) open in a new tab, like the site's other external links
    air.map.getContainer().querySelectorAll(".leaflet-control-attribution a[href^='http']").forEach((a) => {
      a.target = "_blank";
      a.rel = "noopener";
      a.insertAdjacentHTML("beforeend", '<span class="sr-only"> (opens in a new tab)</span>');
    });
  }
  air.map.invalidateSize();     // the tab was hidden when the container was laid out
  if (air.snap) renderMap(air.snap);
}

$("air-rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-hex]");
  const l = tr && air.layers.get(tr.dataset.hex);
  if (!l || !air.map) return;
  // on a phone the list is below the map, so bring the map back into view first
  const box = $("map").getBoundingClientRect();
  if (box.top < 0 || box.bottom > window.innerHeight) {
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("map").scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }
  air.map.setView(l.mark.getLatLng(), Math.max(air.map.getZoom(), l.kind === "rough" ? 5 : 6));
  l.mark.openPopup();
});

hooks.aircraft = (snap) => renderAir(snap);
const prevAirView = hooks.view;
hooks.view = (name) => {
  prevAirView?.(name);
  if (name === "aircraft") airOpen();
};
