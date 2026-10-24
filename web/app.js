"use strict";
const $ = (id) => document.getElementById(id);
const state = { kind: "eam", rows: [], selected: null, oldest: null, msg: null, pick: null, minVotes: 2 };
// Static build for GitHub Pages (tools/build_pages.py): API responses are JSON files, nothing is live.
const DEMO = !!window.SKYWATCH_DEMO;
// trainer.js and aircraft.js register here: hooks.view(name), hooks.aircraft(snapshot)
const hooks = {};

const api = {
  list: (kind, q) => (DEMO ? `api/messages-${kind}.json` : "api/messages?" + q),
  msg: (id) => (DEMO ? `api/messages/${id}.json` : `api/messages/${id}`),
  aircraft: () => (DEMO ? "api/aircraft.json" : "api/aircraft"),
};
const getJSON = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });

const zTime = (epoch, withDate = true) => {
  const d = new Date(epoch * 1000);
  const p = (n) => String(n).padStart(2, "0");
  const t = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}Z`;
  return withDate ? `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${t}` : t;
};
const dur = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// machine copy with the crowd's changes marked
const codeHtml = (body, crowd) => [...(body || "")].map((c, i) => {
  const shown = crowd ? crowd[i] : c;
  if (shown !== c) return `<span class="cr" title="Corrected by listeners">${esc(shown)}</span>`;
  return shown === "?" ? '<span class="q">?</span>' : esc(shown);
}).join("");

// what this browser voted, so the editor can say "you said X" (best effort)
const myVotes = {
  get(id, pos) { try { return JSON.parse(localStorage.getItem("skywatch.votes") || "{}")[`${id}:${pos}`]; } catch { return undefined; } },
  set(id, pos, c) {
    try {
      const all = JSON.parse(localStorage.getItem("skywatch.votes") || "{}");
      all[`${id}:${pos}`] = c;
      localStorage.setItem("skywatch.votes", JSON.stringify(all));
    } catch { /* storage unavailable */ }
  },
};

/* ---------- views ---------- */
const VIEWS = ["monitor", "trainer", "aircraft"];
function route() {
  const name = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "monitor";
  VIEWS.forEach((v) => { $(`view-${v}`).hidden = v !== name; });
  document.querySelectorAll(".views a").forEach((a) => {
    if (a.getAttribute("href") === `#${name}`) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  hooks.view?.(name);
}
window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", route);   // after trainer.js / aircraft.js have registered

/* ---------- live audio + status ---------- */
const live = $("live");
$("listen").addEventListener("click", () => {
  const btn = $("listen");
  if (live.paused) {
    live.src = "live.mp3?t=" + Date.now();     // fresh connection, no stale buffer
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
  const sql = $("sql");
  if (DEMO) {
    sql.textContent = "demo"; sql.className = "sql ended";
    $("src").textContent = "Sample data only. Run Skywatch yourself to monitor 11175 kHz live.";
    return;
  }
  if (s.min_votes) state.minVotes = s.min_votes;
  $("freq").textContent = s.freq_khz;
  $("mode").textContent = (s.mode || "").toUpperCase();
  const name = (s.host || "").startsWith("file:") ? s.host.slice(5) : s.host;
  if (!s.connected && s.finished) {
    sql.textContent = "ended"; sql.className = "sql ended";
    $("src").textContent = `Finished playing ${name}. Restart the server to play it again.`;
  } else if (!s.connected) {
    sql.textContent = "no signal"; sql.className = "sql down";
    $("src").textContent = s.last_error ? `Receiver offline: ${s.last_error}. Retrying.` : "Connecting to receiver…";
  } else {
    sql.textContent = s.squelch_open ? "copying" : "idle";
    sql.className = "sql" + (s.squelch_open ? " open" : "");
    const busy = s.asr_busy ? " · transcribing…" : "";
    const who = s.listeners === 1 ? "1 listener" : `${s.listeners} listeners`;
    const src = name === s.host ? `Receiver ${name}` : `Playing ${name}`;
    $("src").textContent = `${src} · ${who}${busy}`;
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
  if (m.kind === "eam") copy = codeHtml(m.body, m.crowd_body);
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
  const rows = await getJSON(api.list(state.kind, q));
  state.rows = append ? state.rows.concat(rows) : rows;
  state.oldest = state.rows.length ? state.rows[state.rows.length - 1].id : null;
  $("more").hidden = DEMO || rows.length < 50;
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
const fix = $("fix");
let charTimes = [];

// read-only character row (header, the two sends)
function cells(str, small) {
  const wrap = document.createElement("div");
  wrap.className = "cells" + (small ? " small" : "");
  [...str].forEach((c, i) => {
    const s = document.createElement("span");
    s.className = "cell static" + (c === "?" ? " unk" : "");
    s.textContent = c;
    s.dataset.n = i + 1;
    s.setAttribute("aria-label", `Character ${i + 1}: ${c === "?" ? "unknown" : c}`);
    wrap.appendChild(s);
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

const votesAt = (m, i) => (m.corrections || {})[i] || {};

// the merged body: click a character to hear it and correct it
function renderBody() {
  const m = state.msg;
  const wrap = $("body-cells");
  wrap.innerHTML = "";
  [...m.body].forEach((c, i) => {
    const shown = m.crowd_body ? m.crowd_body[i] : c;
    const voted = Object.keys(votesAt(m, i)).length > 0;
    const b = document.createElement("button");
    b.className = "cell" + (shown === "?" ? " unk" : "") + (shown !== c ? " crowd" : "")
      + (voted && shown === c ? " voted" : "") + (state.pick === i ? " picked" : "");
    b.textContent = shown;
    b.dataset.n = i + 1;
    b.dataset.i = i;
    const info = (m.chars || [])[i];
    const alts = info && info.alts && info.alts.length ? `, machine heard ${info.alts.join(" or ")}` : "";
    const crowd = shown !== c ? `, corrected by listeners from ${c}` : "";
    b.setAttribute("aria-label", `Character ${i + 1}: ${shown === "?" ? "unknown" : shown}${alts}${crowd}. Select to hear and correct.`);
    wrap.appendChild(b);
  });
}

function pickChar(i, { play = true, focus = false } = {}) {
  state.pick = i;
  [...$("body-cells").children].forEach((el, k) => el.classList.toggle("picked", k === i));
  const info = (state.msg.chars || [])[i];
  if (play && info) { player.currentTime = Math.max(0, info.t0 - 0.3); player.play().catch(() => {}); }
  renderFix();
  if (focus) $("body-cells").children[i]?.focus();
}

$("pad-copy").addEventListener("click", (e) => {
  const b = e.target.closest("#body-cells .cell");
  if (b) pickChar(+b.dataset.i);
});
$("pad-copy").addEventListener("keydown", (e) => {
  const b = e.target.closest("#body-cells .cell");
  if (!b) return;
  const i = +b.dataset.i;
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    const n = Math.max(0, Math.min(state.msg.body.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)));
    pickChar(n, { play: false, focus: true });
  } else if (/^[a-z0-9]$/i.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey && !DEMO) {
    // typing on a character starts a correction for it
    e.preventDefault();
    if (state.pick !== i) pickChar(i, { play: false });
    $("fix-char").value = e.key.toUpperCase();
    $("fix-char").focus();
  }
});

function renderFix() {
  const m = state.msg, i = state.pick;
  fix.hidden = i == null;
  if (i == null) return;
  const machine = m.body[i];
  const info = (m.chars || [])[i];
  const votes = Object.entries(votesAt(m, i)).sort((a, b) => b[1] - a[1]);
  const shown = m.crowd_body ? m.crowd_body[i] : machine;
  const alts = info && info.alts && info.alts.length ? ` (heard ${info.alts.join(" or ")})` : "";

  $("fix-head").textContent = `Position ${i + 1}`;
  const lines = [`Machine copy: ${machine === "?" ? "unknown" + alts : machine}`];
  lines.push(votes.length
    ? `Listeners: ${votes.map(([c, n]) => `${c} ×${n}`).join(", ")}${shown !== machine ? ` → now ${shown}` : ""}`
    : "No corrections yet.");
  const mine = myVotes.get(m.id, i);
  if (mine) lines.push(`You said ${mine}.`);
  $("fix-info").innerHTML = lines.map((l) => `<span>${esc(l)}</span>`).join("");

  const quick = [...new Set([...(info?.alts || []), ...votes.map(([c]) => c)])].filter((c) => /^[A-Z0-9]$/.test(c)).slice(0, 4);
  $("fix-quick").innerHTML = quick.map((c) => `<button type="button" class="btn small" data-c="${esc(c)}">${esc(c)}</button>`).join("");
  $("fix-char").value = "";
  const form = $("fix-form");
  form.querySelectorAll("input, button").forEach((el) => { el.disabled = DEMO; });
  $("fix-note").textContent = DEMO
    ? "Corrections are saved by a running Skywatch server. This demo is read-only."
    : `A correction takes effect once ${state.minVotes} listeners agree. Arrow keys move between characters; type a letter to correct one.`;
}

async function submitFix(char) {
  const m = state.msg, i = state.pick;
  char = (char || "").trim().toUpperCase();
  if (!/^[A-Z0-9]$/.test(char)) { $("fix-note").textContent = "Enter one letter or digit."; return; }
  try {
    const r = await fetch(`api/messages/${m.id}/corrections`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pos: i, char }),
    });
    const res = await r.json();
    if (!r.ok) throw new Error(res.detail || r.status);
    myVotes.set(m.id, i, char);
    applyCorrection(res);
    $("body-cells").children[i]?.focus();
    const n = (res.corrections[i] || {})[char] || 1;
    $("fix-note").textContent = n >= state.minVotes
      ? `Saved. ${n} listeners agree on ${char}.`
      : `Saved. ${state.minVotes - n} more listener${state.minVotes - n === 1 ? "" : "s"} needed to confirm ${char}.`;
  } catch (err) {
    $("fix-note").textContent = `Couldn't save: ${err.message}`;
  }
}

$("fix-form").addEventListener("submit", (e) => { e.preventDefault(); submitFix($("fix-char").value); });
$("fix-quick").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-c]");
  if (b) submitFix(b.dataset.c);
});
$("fix-char").addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("body-cells").children[state.pick]?.focus();
});

// a correction from this browser or anyone else (SSE)
function applyCorrection(c) {
  const row = state.rows.find((r) => r.id === c.id);
  if (row) { row.crowd_body = c.crowd_body; renderRows(); }
  if (state.msg && state.msg.id === c.id) {
    const keepNote = $("fix-note").textContent;
    state.msg.corrections = c.corrections;
    state.msg.crowd_body = c.crowd_body;
    renderBody();
    if (state.pick != null) { renderFix(); $("fix-note").textContent = keepNote; }
  }
}

async function select(id) {
  state.selected = id;
  document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("sel", +tr.dataset.id === id));
  const m = await getJSON(api.msg(id));
  state.msg = m;
  state.pick = null;
  $("pad-empty").hidden = true;
  $("pad-body").hidden = false;
  const label = { eam: "Emergency Action Message", skyking: "Skyking", voice: "Voice traffic" }[m.kind];
  $("pad-title").textContent = zTime(m.started_at);
  $("pad-meta").textContent = [label, m.callsign && `from ${m.callsign}`, dur(m.duration)].filter(Boolean).join(", ");
  player.src = `audio/${m.audio}`;

  const out = $("pad-copy");
  out.innerHTML = "";
  charTimes = [];
  fix.hidden = true;
  if (m.kind === "eam" && m.body) {
    if (m.header) out.appendChild(block("Header", cells(m.header, false)));
    charTimes = m.chars || [];
    const body = document.createElement("div");
    body.className = "cells";
    body.id = "body-cells";
    const unknown = (m.crowd_body || m.body).split("?").length - 1;
    const hint = unknown ? ` · ${unknown} unknown, select one to help copy it` : "";
    out.appendChild(block(`Message, ${m.body.length} of ${m.expected_len} characters${hint}`, body));
    renderBody();
    out.appendChild(fix);
    if (m.copy2) {
      const c = document.createElement("div");
      c.className = "copies";
      c.innerHTML = `<div><div class="lbl">First send (machine)</div></div><div><div class="lbl">After “I say again” (machine)</div></div>`;
      c.children[0].appendChild(cells(m.copy1 || "", true));
      c.children[1].appendChild(cells(m.copy2 || "", true));
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
  document.querySelectorAll("#body-cells .cell").forEach((el, i) => el.classList.toggle("playing", i === idx));
});

/* ---------- server events ---------- */
function connectEvents() {
  const es = new EventSource("api/events");
  es.addEventListener("status", (e) => renderStatus(JSON.parse(e.data)));
  es.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (state.kind !== "all" && state.kind !== m.kind) return;
    state.rows.unshift(m);
    renderRows(m.id);
    if (m.kind === "eam") select(m.id);
  });
  es.addEventListener("correction", (e) => applyCorrection(JSON.parse(e.data)));
  es.addEventListener("aircraft", (e) => hooks.aircraft?.(JSON.parse(e.data)));
}

if (DEMO) {
  $("listen").hidden = true;
  document.querySelector(".meter").hidden = true;
  renderStatus({});
} else {
  getJSON("api/status").then(renderStatus).catch(() => {});
  connectEvents();
}
loadRows();
