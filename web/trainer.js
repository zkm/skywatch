"use strict";
/* Copy trainer: play a real EAM recording, write it down, compare against the
   reference copy (machine copy with listeners' corrections). Runs entirely in
   the browser, so it works in the static demo too. */
const trainer = { pool: null, msg: null, ref: "", checked: false };

function trInputs() { return [...$("tr-grid").querySelectorAll("input")]; }

async function trStart() {
  if (!trainer.pool) {
    const q = new URLSearchParams({ kind: "eam", limit: 200 });
    trainer.pool = (await getJSON(api.list("eam", q))).filter((m) => m.body);
  }
  const empty = !trainer.pool.length;
  $("tr-empty").hidden = !empty;
  $("tr-body").hidden = empty;
  if (empty) return;
  $("tr-next").hidden = trainer.pool.length < 2;
  const others = trainer.pool.filter((m) => !trainer.msg || m.id !== trainer.msg.id);
  const pick = others[Math.floor(Math.random() * others.length)];
  trLoad(await getJSON(api.msg(pick.id)));
}

function trLoad(m) {
  trainer.msg = m;
  trainer.ref = m.crowd_body || m.body;
  trainer.checked = false;
  const p = $("tr-player");
  p.src = `audio/${m.audio}`;
  p.playbackRate = +$("tr-rate").value;
  $("tr-meta").textContent = `Recorded ${zTime(m.started_at)}${m.callsign ? ` from ${m.callsign}` : ""} · ${trainer.ref.length} characters`
    + (credit(m) ? ` · ${credit(m)}` : "");
  const grid = $("tr-grid");
  grid.innerHTML = "";
  [...trainer.ref].forEach((_, i) => {
    const wrap = document.createElement("span");
    wrap.className = "cell tr-cell";
    wrap.dataset.n = i + 1;
    wrap.innerHTML = `<input maxlength="1" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-label="Character ${i + 1}">`;
    grid.appendChild(wrap);
  });
  $("tr-result").hidden = true;
  $("tr-score").textContent = "";
  $("tr-share").hidden = true;
}

const trFocus = (i) => { const el = trInputs()[i]; if (el) { el.focus(); el.select(); } };

$("tr-grid").addEventListener("input", (e) => {
  const inputs = trInputs(), i = inputs.indexOf(e.target);
  if (i < 0) return;
  const v = e.target.value.toUpperCase().replace(/[^A-Z0-9?]/g, "");
  e.target.value = v.slice(-1);
  if (trainer.checked) trClearMarks();
  if (v) trFocus(i + 1);
});
$("tr-grid").addEventListener("keydown", (e) => {
  const inputs = trInputs(), i = inputs.indexOf(e.target);
  if (i < 0) return;
  if (e.key === "Backspace" && !e.target.value) { e.preventDefault(); trFocus(i - 1); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); trFocus(i - 1); }
  else if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); trFocus(i + 1); }
  else if (e.key === "Enter") { e.preventDefault(); trCheck(); }
});
// pasting a whole copy fills from the focused cell onward
$("tr-grid").addEventListener("paste", (e) => {
  const inputs = trInputs(), i = inputs.indexOf(e.target);
  if (i < 0) return;
  e.preventDefault();
  const text = (e.clipboardData.getData("text") || "").toUpperCase().replace(/[^A-Z0-9?]/g, "");
  [...text].forEach((c, k) => { if (inputs[i + k]) inputs[i + k].value = c; });
  trFocus(Math.min(inputs.length - 1, i + text.length));
});

function trClearMarks() {
  trainer.checked = false;
  $("tr-grid").querySelectorAll(".tr-cell").forEach((c, i) => {
    c.classList.remove("ok", "bad", "miss", "nokey"); delete c.dataset.ref;
    c.querySelector("input").setAttribute("aria-label", `Character ${i + 1}`);
  });
  $("tr-result").hidden = true;
  $("tr-score").textContent = "";
  $("tr-share").hidden = true;
}

function trCheck() {
  const ref = trainer.ref, inputs = trInputs();
  let right = 0, known = 0, helped = 0;
  inputs.forEach((el, i) => {
    const cell = el.parentElement, mine = el.value, want = ref[i];
    cell.classList.remove("ok", "bad", "miss", "nokey");
    delete cell.dataset.ref;
    const n = `Character ${i + 1}`;
    if (want === "?") {                       // nobody knows this one yet
      cell.classList.add("nokey");
      el.setAttribute("aria-label", `${n}: not known to anyone yet`);
      if (/^[A-Z0-9]$/.test(mine)) helped++;
      return;
    }
    known++;
    if (mine === want) { right++; cell.classList.add("ok"); el.setAttribute("aria-label", `${n}: correct`); }
    else {
      cell.classList.add(mine ? "bad" : "miss"); cell.dataset.ref = want;
      el.setAttribute("aria-label", mine ? `${n}: you wrote ${mine}, it was ${want}` : `${n}: missed, it was ${want}`);
    }
  });
  trainer.checked = true;
  const pct = known ? Math.round((100 * right) / known) : 0;
  $("tr-score").textContent = `${right} of ${known} known characters correct (${pct}%).`
    + (known < ref.length ? ` ${ref.length - known} position${ref.length - known === 1 ? " is" : "s are"} still unknown to everyone.` : "");
  const res = $("tr-result");
  res.hidden = false;
  res.querySelector(".cells")?.remove();
  res.appendChild(cells(ref, true));
  const share = $("tr-share");
  share.hidden = !helped;
  if (helped) {
    $("tr-share-text").textContent = DEMO
      ? `You filled in ${helped} position${helped === 1 ? "" : "s"} nobody has copied yet. On a running Skywatch server you could send ${helped === 1 ? "it" : "them"} as corrections.`
      : `You filled in ${helped} position${helped === 1 ? "" : "s"} nobody has copied yet. Send ${helped === 1 ? "it" : "them"} as corrections?`;
    $("tr-send").hidden = DEMO;
    $("tr-send").disabled = false;
  }
}

async function trSend() {
  const btn = $("tr-send");
  btn.disabled = true;
  const inputs = trInputs();
  let sent = 0, failed = "";
  for (let i = 0; i < inputs.length; i++) {
    const c = inputs[i].value;
    if (trainer.ref[i] !== "?" || !/^[A-Z0-9]$/.test(c)) continue;
    const r = await fetch(`api/messages/${trainer.msg.id}/corrections`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pos: i, char: c }),
    });
    const res = await r.json();
    if (!r.ok) { failed = res.detail || String(r.status); break; }
    myVotes.set(trainer.msg.id, i, c);
    applyCorrection(res);
    sent++;
  }
  $("tr-share-text").textContent = failed
    ? `Sent ${sent}, then stopped: ${failed}.`
    : `Sent ${sent} correction${sent === 1 ? "" : "s"}. Thanks for copying.`;
}

$("tr-check").addEventListener("click", trCheck);
$("tr-clear").addEventListener("click", () => { trInputs().forEach((el) => { el.value = ""; }); trClearMarks(); trFocus(0); });
$("tr-next").addEventListener("click", trStart);
$("tr-send").addEventListener("click", trSend);
$("tr-rate").addEventListener("change", (e) => { $("tr-player").playbackRate = +e.target.value; });

const prevView = hooks.view;
hooks.view = (name) => {
  prevView?.(name);
  if (name !== "trainer") { $("tr-player").pause(); return; }
  if (!trainer.msg) trStart().catch(() => { $("tr-empty").hidden = false; $("tr-body").hidden = true; });
};
