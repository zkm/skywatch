"use strict";
const $ = (id) => document.getElementById(id);
const state = { kind: "eam", rows: [], selected: null, oldest: null };

const zTime = (epoch, withDate = true) => {
  const d = new Date(epoch * 1000);
  const p = (n) => String(n).padStart(2, "0");
  const t = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}Z`;
  return withDate ? `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${t}` : t;
};
const dur = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const codeHtml = (s) => esc(s).replace(/\?/g, '<span class="q">?</span>');

/* ---------- live audio + status ---------- */
const live = $("live");
$("listen").addEventListener("click", () => {
  const btn = $("listen");
  if (live.paused) {
    live.src = "/live.mp3?t=" + Date.now();     // fresh connection, no stale buffer
    live.play().catch(() => {});
    btn.setAttribute("aria-pressed", "true");
    $("listen-label").textContent = "Stop";
  } else {
    live.pause(); live.removeAttribute("src"); live.load();
    btn.setAttribute("aria-pressed", "false");
    $("listen-label").textContent = "Listen live";
  }
});

function renderStatus(s) {
  $("freq").textContent = s.freq_khz;
  $("mode").textContent = (s.mode || "").toUpperCase();
  const sql = $("sql");
  if (!s.connected) {
    sql.textContent = "no signal"; sql.className = "sql down";
    $("src").textContent = s.last_error ? `Receiver offline: ${s.last_error}. Retrying.` : "Connecting to receiver…";
  } else {
    sql.textContent = s.squelch_open ? "copying" : "idle";
    sql.className = "sql" + (s.squelch_open ? " open" : "");
    const busy = s.asr_busy ? " Transcribing…" : "";
    $("src").textContent = `Receiver ${s.host}. ${s.listeners} listening.${busy}`;
  }
  // meter spans floor .. floor+24 dB, threshold mark at open_db
  if (s.floor_db != null) {
    const span = 24;
    const frac = Math.max(0, Math.min(1, (s.level_db - s.floor_db) / span));
    $("meter").style.width = `${frac * 100}%`;
    $("thresh").style.left = `${(s.open_db / span) * 100}%`;
  }
}

/* ---------- log ---------- */
function rowHtml(m, isNew) {
  let copy;
  if (m.kind === "eam") copy = codeHtml(m.body || "");
  else if (m.kind === "skyking") {
    const k = m.skyking || {};
    copy = `<span class="kind">Skyking</span>${esc(k.codeword || "?")}, time ${esc(k.time || "??")}, auth ${esc(k.authentication || "??")}`;
  } else copy = `<span class="kind">Voice</span>`;
  return `<tr data-id="${m.id}" tabindex="0" class="${isNew ? "new" : ""} ${state.selected === m.id ? "sel" : ""}">
    <td class="t">${zTime(m.started_at)}</td>
    <td>${esc(m.callsign || "—")}</td>
    <td class="code">${copy}</td>
    <td class="num">${m.kind === "eam" && m.body ? `${m.body.length}/${m.expected_len}` : dur(m.duration)}</td></tr>`;
}

function renderRows(newId) {
  const tb = $("rows");
  if (!state.rows.length) {
    tb.innerHTML = `<tr class="empty-row"><td colspan="4">Nothing copied yet. Leave the page open; new traffic appears here automatically.</td></tr>`;
    return;
  }
  tb.innerHTML = state.rows.map((m) => rowHtml(m, m.id === newId)).join("");
}

async function loadRows(append = false) {
  const q = new URLSearchParams({ kind: state.kind, limit: 50 });
  if (append && state.oldest) q.set("before", state.oldest);
  const rows = await fetch("/api/messages?" + q).then((r) => r.json());
  state.rows = append ? state.rows.concat(rows) : rows;
  state.oldest = state.rows.length ? state.rows[state.rows.length - 1].id : null;
  $("more").hidden = rows.length < 50;
  renderRows();
  if (!append && !state.selected && state.rows.length) select(state.rows[0].id);
}

$("rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) select(+tr.dataset.id);
});
$("rows").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(+tr.dataset.id); }
});
$("more").addEventListener("click", () => loadRows(true));
document.querySelectorAll(".filters button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".filters button").forEach((x) => x.setAttribute("aria-selected", x === b));
    state.kind = b.dataset.kind;
    loadRows();
  }));

/* ---------- copy pad ---------- */
const player = $("player");
let charTimes = [];

function cells(str, chars, small) {
  const wrap = document.createElement("div");
  wrap.className = "cells" + (small ? " small" : "");
  [...str].forEach((c, i) => {
    const b = document.createElement("button");
    const info = chars && chars[i];
    b.className = "cell" + (c === "?" ? " unk" : "") + (info ? "" : " static");
    b.textContent = c;
    b.dataset.n = i + 1;
    const alts = info && info.alts && info.alts.length ? `, heard as ${info.alts.join(" or ")}` : "";
    b.setAttribute("aria-label", `Character ${i + 1}: ${c === "?" ? "unknown" : c}${alts}`);
    if (info) b.addEventListener("click", () => { player.currentTime = Math.max(0, info.t0 - 0.3); player.play(); });
    else b.tabIndex = -1;
    wrap.appendChild(b);
  });
  return wrap;
}

function block(title, el) {
  const d = document.createElement("div");
  d.className = "copy-block";
  d.innerHTML = `<h3>${esc(title)}</h3>`;
  d.appendChild(el);
  return d;
}

async function select(id) {
  state.selected = id;
  document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("sel", +tr.dataset.id === id));
  const m = await fetch(`/api/messages/${id}`).then((r) => r.json());
  $("pad-empty").hidden = true;
  $("pad-body").hidden = false;
  const label = { eam: "Emergency Action Message", skyking: "Skyking", voice: "Voice traffic" }[m.kind];
  $("pad-title").textContent = zTime(m.started_at);
  $("pad-meta").textContent = [label, m.callsign && `from ${m.callsign}`, dur(m.duration)].filter(Boolean).join(", ");
  player.src = `/audio/${m.audio}`;

  const out = $("pad-copy");
  out.innerHTML = "";
  charTimes = [];
  if (m.kind === "eam") {
    if (m.header) out.appendChild(block("Header", cells(m.header, null, false)));
    charTimes = m.chars || [];
    out.appendChild(block(`Message, ${m.body.length} of ${m.expected_len} characters`, cells(m.body || "", m.chars, false)));
    const conflicts = (m.chars || []).map((c, i) => c.c === "?" && c.alts.length ? `position ${i + 1} heard as ${c.alts.join(" or ")}` : null).filter(Boolean);
    if (conflicts.length) {
      const p = document.createElement("p");
      p.className = "alts";
      p.textContent = `Machine guesses: ${conflicts.join("; ")}.`;
      out.appendChild(p);
    }
    if (m.copy2) {
      const c = document.createElement("div");
      c.className = "copies";
      c.innerHTML = `<div><div class="lbl">First send</div></div><div><div class="lbl">After “I say again”</div></div>`;
      c.children[0].appendChild(cells(m.copy1 || "", null, true));
      c.children[1].appendChild(cells(m.copy2 || "", null, true));
      out.appendChild(c);
    }
  } else if (m.kind === "skyking") {
    const k = m.skyking || {};
    out.innerHTML = `<dl class="sky">
      <div><dt>Codeword</dt><dd>${esc(k.codeword || "?")}</dd></div>
      <div><dt>Time (minutes past)</dt><dd>${esc(k.time || "??")}</dd></div>
      <div><dt>Authentication</dt><dd>${esc(k.authentication || "??")}</dd></div></dl>`;
  }

  const words = m.words || [];
  $("pad-transcript").innerHTML = words.length
    ? words.map((w) => `<span class="${w.prob < 0.45 ? "lowp" : ""}" title="${Math.round(w.prob * 100)}%">${esc(w.text)}</span>`).join(" ")
    : esc(m.transcript || "No transcript.");
}

// highlight the character being spoken during playback
player.addEventListener("timeupdate", () => {
  const t = player.currentTime;
  const idx = charTimes.findIndex((c) => t >= c.t0 && t <= c.t1 + 0.15);
  const grid = document.querySelectorAll("#pad-copy .cell:not(.static)");
  grid.forEach((el, i) => el.classList.toggle("playing", i === idx));
});

/* ---------- server events ---------- */
function connectEvents() {
  const es = new EventSource("/api/events");
  es.addEventListener("status", (e) => renderStatus(JSON.parse(e.data)));
  es.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (state.kind !== "all" && state.kind !== m.kind) return;
    state.rows.unshift(m);
    renderRows(m.id);
    if (m.kind === "eam") select(m.id);
  });
}

fetch("/api/status").then((r) => r.json()).then(renderStatus).catch(() => {});
loadRows();
connectEvents();
