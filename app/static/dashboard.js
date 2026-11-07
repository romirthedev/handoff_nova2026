// Ticket rail for the pass: live state over a websocket, voice commands, and plate-check results from the phone.
const $ = (id) => document.getElementById(id);
const KID = location.pathname.split("/")[2];
const API = `/api/k/${KID}`;
const STATUS = { new: "New", cooking: "Cooking", ready: "Ready", issue: "Issue", verified: "Checked", picked_up: "Picked up", cancelled: "Cancelled" };
const FILTERS = [["open", "Open"], ["new", "New"], ["cooking", "Cooking"], ["ready", "Ready"], ["issue", "Issue"], ["verified", "Checked"], ["out", "Out"]];
const isOpen = (o) => o.status !== "picked_up" && o.status !== "cancelled";
const inFilter = (o, f) => (f === "open" ? isOpen(o) : f === "out" ? !isOpen(o) : o.status === f);
const EXAMPLES = ["Start the timer for order 45 steak", "What should I start next?", "Dasher for order 45 is 10 minutes away",
                  "Push order 50 back 10 minutes", "For all orders with garlic bread start the garlic bread timer",
                  "How long on order 45?", "Order 45 picked up"];

let state = null, menu = {}, sources = [], offset = 0, filter = "open", focusNum = null;
const flashes = {};            // order num -> {cls, until}
const freshUp = {};            // item uid -> until (pulse the UP badge)
const seen = new Set();        // tickets already on screen, so only new ones animate in
let lastScanId = null;
const orbs = {};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const now = () => Date.now() + offset;
const mmss = (s) => { s = Math.max(0, Math.ceil(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const hm = (ms) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const ago = (ms) => { const m = Math.floor((now() - ms) / 60000); return m < 1 ? "just now" : m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`; };

function toast(text, bad) {
  const t = $("toast"); t.textContent = text; t.className = "toast on" + (bad ? " bad" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), 3200);
}
function say(text) { $("say").textContent = text; }
function orb(name, s) { if (orbs[name]) orbs[name].set(s); }

// ------------------------------------------------------------------ speech out
let speaking = false;
function speak(text) {
  if (!$("speak").checked || !("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/×/g, " ").replace(/#/g, "number "));
  u.rate = 1.05;
  u.onstart = () => { speaking = true; pauseRec(); };
  u.onend = u.onerror = () => { speaking = false; resumeRec(); };
  speechSynthesis.speak(u);
}
function chime(ok = true) {
  try {
    const ctx = chime.ctx || (chime.ctx = new AudioContext());
    const notes = ok ? [880, 1320] : [330, 247];
    notes.forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f; o.type = "sine";
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.14);
      g.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + i * 0.14 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.14 + 0.32);
      o.connect(g).connect(ctx.destination); o.start(ctx.currentTime + i * 0.14); o.stop(ctx.currentTime + i * 0.14 + 0.35);
    });
  } catch {}
}

// ------------------------------------------------------------------ rendering
function itemRow(o, it, missing) {
  const m = menu[it.menu] || { name: it.menu, emoji: "·" };
  const mod = it.mod ? `<small>${esc(it.mod)}</small>` : "";
  let timer;
  if (it.timer && !it.done) {
    const end = it.timer.start + it.timer.secs * 1000;
    timer = `<button class="timer run" data-act="stop" data-item="${it.menu}" data-end="${end}" data-total="${it.timer.secs}" title="Stop timer">${mmss((end - now()) / 1000)}</button>`;
  } else if (it.done && it.timer) {
    timer = `<button class="timer up${(freshUp[it.uid] || 0) > Date.now() ? " fresh" : ""}" data-act="start" data-item="${it.menu}" title="Restart timer">UP</button>`;
  } else if (it.minutes) {
    timer = `<button class="timer" data-act="start" data-item="${it.menu}" title="Start ${it.minutes}-minute timer">▷ ${it.minutes}:00</button>`;
  } else {
    timer = `<span class="timer none" title="No cook time">—</span>`;
  }
  return `<li class="${missing ? "miss" : ""}"><span class="q">${it.qty}×</span><span class="e">${m.emoji}</span>
    <span class="n">${esc(m.name)}${mod}</span>${timer}<span class="bar"></span></li>`;
}

// when to start an order so it's packed as its Dasher walks in: pickup time minus the slowest dish minus packing
function planHTML(o) {
  if (!o.pickup_at || !isOpen(o)) return "";
  const who = o.courier ? (o.source === "DoorDash" ? "Dasher" : "Courier") : "Pickup";
  return `<div class="plan" data-pickup="${o.pickup_at}" data-who="${who}"${o.status === "new" ? ` data-start="${o.start_at}"` : ""}>
    <span class="go"></span><span class="eta"></span>
    <span class="nudge"><button data-act="eta" data-min="-5" title="${who} 5 minutes sooner">−5</button><button data-act="eta" data-min="5" title="${who} 5 minutes later">+5</button></span></div>`;
}
const startState = (d) => (d > 120 ? "" : d > 0 ? "soon" : d > -60 ? "now" : "late");
function paintPlan(el) {
  const t = now(), p = +el.dataset.pickup, go = el.querySelector(".go"), eta = el.querySelector(".eta");
  if (el.dataset.start) {
    const d = (+el.dataset.start - t) / 1000;
    go.textContent = d > 0 ? `Start in ${mmss(d)}` : d > -60 ? "Start now" : `Late ${Math.floor(-d / 60)}m · start now`;
    go.className = "go " + startState(d);
  } else { go.textContent = ""; go.className = "go"; }
  const pm = Math.round((p - t) / 60000);
  eta.textContent = `${el.dataset.who} ${hm(p)} · ` + (pm > 0 ? `${pm}m` : pm === 0 ? "now" : `${-pm}m late`);
  eta.className = "eta" + (pm < 0 ? " late" : "");
}

function ticketHTML(o) {
  const age = (now() - o.placed) / 60000;
  const missing = new Set((o.check && !o.check.ok ? o.check.missing : []).map((x) => x.id));
  const fl = flashes[o.num] && flashes[o.num].until > Date.now() ? " " + flashes[o.num].cls : "";
  const who = o.courier ? `${o.source === "DoorDash" ? "Dasher" : "Courier"} ${esc(o.courier)}` : (o.source === "Phone" ? "Phone pickup" : o.source === "Website" ? "Online pickup" : "At the counter");
  let check = "";
  if (o.check) check = `<div class="check ${o.check.ok ? "ok" : "bad"}">${o.check.ok ? "✓ Checked · all items" : "✕ " + esc(o.check.reason)} · ${ago(o.check.at)}</div>`;
  const timed = o.items.some((it) => it.minutes && !(it.timer && !it.done));
  const enter = seen.has(o.num) ? "" : " enter";
  seen.add(o.num);
  return `<article class="ticket s-${o.status}${fl}${enter}${focusNum === o.num ? " focus" : ""}" id="t-${o.num}" data-num="${o.num}">
    <header><span class="num">#${o.num}</span><span>${esc(o.source)}</span><span class="pill">${STATUS[o.status]}</span>
      <span class="age ${age > 20 ? "late" : age > 12 ? "warm" : ""}" data-placed="${o.placed}">${ago(o.placed)}</span></header>
    <h3>${esc(o.guest)}</h3>
    <div class="sub">${who}</div>
    ${planHTML(o)}
    <ul class="items">${o.items.map((it) => itemRow(o, it, missing.has(it.menu))).join("")}</ul>
    ${check}
    <footer>
      ${timed && isOpen(o) ? `<button data-act="start">Fire all</button>` : ""}
      ${["new", "cooking"].includes(o.status) ? `<button data-act="ready">Ready</button>` : ""}
      ${isOpen(o) ? `<button data-act="picked_up">Handed off</button>` : `<button data-act="reopen">Reopen</button>`}
    </footer></article>`;
}

function render() {
  if (!state) return;
  const k = state.kitchen, orders = Object.values(k.orders).sort((a, b) => a.placed - b.placed);
  document.title = `${k.name} · Handoff`;
  $("kname").textContent = k.name;
  const count = (f) => orders.filter((o) => inFilter(o, f)).length;
  $("filters").innerHTML = FILTERS.map(([f, label]) => `<button type="button" data-f="${f}" aria-pressed="${f === filter}">${label}<b>${count(f)}</b></button>`).join("");
  const shown = orders.filter((o) => inFilter(o, filter));
  $("rail").innerHTML = shown.length ? shown.map(ticketHTML).join("") :
    `<div class="empty">${filter === "open" ? "No open tickets. Add one with + New order." : "Nothing here right now."}</div>`;
  const next = orders.filter((o) => o.status === "new" && o.start_at).sort((a, b) => a.start_at - b.start_at).slice(0, 4);
  $("queue").innerHTML = next.length ? next.map((o) => {
    const slow = o.items.reduce((a, it) => ((it.minutes || 0) > (a.minutes || 0) ? it : a), o.items[0]);
    const who = o.courier ? (o.source === "DoorDash" ? "Dasher" : "Courier") : "Pickup";
    return `<li><button type="button" data-num="${o.num}"><span class="qn">#${o.num}</span>
      <span class="qg">${esc(o.guest)}<small>${esc((menu[slow.menu] || {}).name || "")}${slow.minutes ? ` ${slow.minutes} min` : ""} · ${who} ${hm(o.pickup_at)}</small></span>
      <span class="when" data-start="${o.start_at}"></span></button></li>`;
  }).join("") : `<li class="none">Nothing waiting to start.</li>`;
  document.querySelectorAll("#speed button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.speed === (k.speed || 1))));

  const cams = state.cameras;
  $("camdot").className = "live" + (cams ? " on" : "");
  $("camtext").textContent = cams ? `${cams} camera${cams > 1 ? "s" : ""} live` : "No camera";
  $("log").innerHTML = k.log.slice().reverse().map((l) => `<li><span>${new Date(l.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>${esc(l.text)}</li>`).join("");
  if (!$("last").dataset.busy && (k.scans[0] || {}).id !== lastScanId) renderLast(k.scans[0]);
  tickClock();
}

function renderLast(r) {
  const el = $("last");
  lastScanId = r ? r.id : null;
  if (!r) { el.innerHTML = `<div class="result waiting">Waiting for the first order under the camera.</div>`; return; }
  const lines = [
    ...r.present.map((x) => `<li class="p">✓ ${x.qty}× ${x.emoji} ${esc(x.name)}</li>`),
    ...r.missing.map((x) => `<li class="m">✕ missing ${x.qty}× ${x.emoji} ${esc(x.name)}</li>`),
    ...r.extra.map((x) => `<li class="x">+ extra ${x.qty}× ${x.emoji} ${esc(x.name)}</li>`),
  ].join("");
  const head = r.ok ? "✓ Good to go" : r.order ? (r.missing.length ? "✕ Missing items" : "✕ Wrong items") : "✕ No match";
  el.innerHTML = `<div class="result ${r.ok ? "ok" : "bad"}"><div class="big">${head}</div>
    <div class="who">${r.order ? `${esc(r.guest)} · #${r.order} · ${esc(r.source)}` : esc(r.reason)}</div>
    ${lines ? `<ul>${lines}</ul>` : ""}
    <div class="meta">${r.seconds}s · ${esc(r.engine)}${r.by_tag ? " · read bag label" : ""} · ${ago(r.at)}${r.alternatives && r.alternatives.length ? ` · also fits #${r.alternatives.join(", #")}` : ""}${r.warning ? ` · ${esc(r.warning)}` : ""}</div></div>`;
}

function tickClock() {
  $("clock").textContent = new Date(now()).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  document.querySelectorAll(".timer.run[data-end]").forEach((b) => {
    const left = (+b.dataset.end - now()) / 1000;
    b.textContent = mmss(left);
    const bar = b.parentElement.querySelector(".bar");
    if (bar) bar.style.width = `${Math.min(100, 100 * (1 - left / +b.dataset.total))}%`;
  });
  document.querySelectorAll(".plan[data-pickup]").forEach(paintPlan);
  document.querySelectorAll(".when[data-start]").forEach((w) => {
    const d = (+w.dataset.start - now()) / 1000;
    w.textContent = d > 0 ? `in ${mmss(d)}` : d > -60 ? "now" : `${Math.floor(-d / 60)}m late`;
    w.className = "when " + startState(d);
  });
  document.querySelectorAll(".age[data-placed]").forEach((a) => {
    const m = (now() - +a.dataset.placed) / 60000;
    a.textContent = ago(+a.dataset.placed);
    a.className = "age " + (m > 20 ? "late" : m > 12 ? "warm" : "");
  });
}
setInterval(tickClock, 250);

function flash(num, ok) {
  flashes[num] = { cls: ok ? "flash-ok" : "flash-bad", until: Date.now() + 3400 };
  if (state && state.kitchen.orders[num] && !inFilter(state.kitchen.orders[num], filter)) filter = "open";
  render();
  const el = $("t-" + num);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
}
function focusTicket(num) {
  focusNum = num; render();
  const el = $("t-" + num);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  clearTimeout(focusTicket.t); focusTicket.t = setTimeout(() => { focusNum = null; render(); }, 6000);
}

function banner(r) {
  const b = $("banner");
  const text = r.ok ? `✓ Good to go · ${esc(r.guest)}` : r.order ? `✕ ${esc(r.guest)} · ${esc(r.reason)}` : `✕ ${esc(r.reason)}`;
  b.innerHTML = `${text}${r.order ? `<small>#${r.order} · ${esc(r.source)}</small>` : ""}`;
  b.className = "banner on " + (r.ok ? "ok" : "bad");
  clearTimeout(banner.t); banner.t = setTimeout(() => (b.className = "banner " + (r.ok ? "ok" : "bad")), 4200);
}

// ------------------------------------------------------------------ server
async function post(path, body) {
  const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || "Request failed");
  return j;
}

async function runCommand(text, spoken) {
  text = text.trim();
  if (!text) return;
  $("heard").innerHTML = `“${esc(text)}”<span class="reply">…</span>`;
  try {
    const r = await post("/command", { text });
    if (!r.ok && spoken && !r.parsed.intent && !r.parsed.order) {   // background chatter, not a command
      $("heard").innerHTML = `<span class="interim">“${esc(text)}”</span><span class="reply">Not a command, ignored.</span>`;
      return;
    }
    $("heard").innerHTML = `“${esc(text)}”<span class="reply${r.ok ? "" : " no"}">${esc(r.say)}</span>`;
    say(r.say);
    speak(r.say);
    if (r.order) focusTicket(r.order);
  } catch (e) { toast(e.message, true); }
}

let ws, wsTries = 0;
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/k/${KID}?role=dashboard`);
  ws.onopen = () => { wsTries = 0; $("live").className = "live on"; $("conn").textContent = "Live"; };
  ws.onclose = () => {
    $("live").className = "live"; $("conn").textContent = "Reconnecting";
    setTimeout(connect, Math.min(8000, 500 * 2 ** wsTries++));
  };
  ws.onmessage = (e) => {
    if (e.data === "pong") return;
    const m = JSON.parse(e.data);
    if (m.type === "state") {
      const first = !state;
      offset = m.now - Date.now(); state = m; render();
      if (first) say(`${m.kitchen.name} · ${Object.values(m.kitchen.orders).filter(isOpen).length} open tickets` + (m.vision ? "" : " · vision off: only card codes are read"));
    } else if (m.type === "scanning") {
      $("last").dataset.busy = "1";
      $("last").innerHTML = `<div class="result waiting"><span class="orb-slot s20" id="scan-orb"><span class="cursor"></span></span>Checking an order against the tickets…</div>`;
      if (window.HOrbs) window.HOrbs.mount($("scan-orb"), "working", 20);
      orb("status", "working"); say("Checking an order at the pass…");
    } else if (m.type === "scan_failed") {
      delete $("last").dataset.busy; orb("status", "breathing"); lastScanId = undefined; render();
      say(`${state.kitchen.name} · watching the pass`);
      if (!m.quiet) toast("The camera photo couldn't be read. Try again.", true);
    } else if (m.type === "scan") {
      const r = m.result;
      delete $("last").dataset.busy; orb("status", "breathing");
      renderLast(r); banner(r);  // the phone at the pass reads results aloud; the laptop stays quiet
      say(r.ok ? `✓ #${r.order} ${r.guest} · good to go` : `✕ ${r.order ? `#${r.order} ${r.guest} · ` : ""}${r.reason}`);
      if (r.order) flash(r.order, r.ok);
    } else if (m.type === "timer_done") {
      freshUp[m.item] = Date.now() + 15000; chime(true); say(m.say); speak(m.say); toast("⏲ " + m.say);
    } else if (m.type === "start_now") {
      chime(true); say(m.say); speak(m.say); toast("▶ " + m.say);
    } else if (m.type === "new_order") {
      say(m.say); chime(true);
    }
  };
}
setInterval(() => ws && ws.readyState === 1 && ws.send("ping"), 25000);

// ------------------------------------------------------------------ voice in
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false;
function pauseRec() { if (rec && listening) try { rec.abort(); } catch {} }
function resumeRec() { if (rec && listening && !speaking) try { rec.start(); } catch {} }

function setListening(on) {
  if (!SR) { toast("Voice needs Chrome or Edge. Type commands in the box instead.", true); return; }
  listening = on;
  $("mic").textContent = on ? "Listening · tap to stop" : "Start listening";
  $("mic").classList.toggle("on", on);
  orb("mic", on ? "listening" : "breathing");
  if (on) {
    if (!rec) {
      rec = new SR();
      rec.lang = "en-US"; rec.continuous = true; rec.interimResults = true;
      rec.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) runCommand(r[0].transcript, true); else interim += r[0].transcript;
        }
        if (interim) $("heard").innerHTML = `<span class="interim">${esc(interim)}…</span>`;
      };
      rec.onerror = (e) => {
        if (e.error === "not-allowed" || e.error === "service-not-allowed") { toast("Microphone blocked. Allow it in the address bar.", true); setListening(false); }
      };
      rec.onend = () => { if (listening && !speaking) setTimeout(resumeRec, 150); };
    }
    resumeRec();
    $("heard").innerHTML = `<span class="interim">Listening. Try “start the timer for order 45 steak”.</span>`;
  } else if (rec) { try { rec.abort(); } catch {} }
}
$("mic").addEventListener("click", () => setListening(!listening));
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.target.closest("input, textarea, select, button, dialog")) { e.preventDefault(); setListening(!listening); }
});
if (!SR) $("heard").innerHTML = `<span class="interim">Voice works in Chrome or Edge. You can type commands here too.</span>`;

$("cmdform").addEventListener("submit", (e) => { e.preventDefault(); runCommand($("cmd").value); $("cmd").value = ""; });
$("examples").innerHTML = EXAMPLES.map((x) => `<li><button type="button">${esc(x)}</button></li>`).join("");
$("examples").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) runCommand(b.textContent); });

// ------------------------------------------------------------------ ticket buttons
$("rail").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const num = b.closest(".ticket").dataset.num;
  try {
    const min = b.dataset.min ? +b.dataset.min : null;  // the ±5 pickup buttons
    const r = await post("/action", { intent: b.dataset.act, order: num, item: b.dataset.item || null,
                                      minutes: min && Math.abs(min), shift: min && Math.sign(min) });
    if (r.say) { say(r.say); if (!r.ok) toast(r.say, true); }
  } catch (err) { toast(err.message, true); }
});
$("queue").addEventListener("click", (e) => { const b = e.target.closest("button[data-num]"); if (b) focusTicket(+b.dataset.num); });
$("filters").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { filter = b.dataset.f; render(); } });
$("speed").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) post("/settings", { speed: +b.dataset.speed }); });
$("reset").addEventListener("click", async () => { if (confirm("Put all 25 demo tickets back to new?")) { await post("/reset"); toast("Demo tickets reset"); } });

// ------------------------------------------------------------------ new order dialog
const DONENESS = { ribeye: ["rare", "medium rare", "medium", "medium well", "well done"], burger: ["medium rare", "medium", "medium well", "well done"] };
const NAMES = ["Riley Park", "Nora Bell", "Theo Grant", "Lena Ford", "Kai Morgan", "Zoe Price", "Max Hayes", "Ivy Cole", "Leo Reyes", "Ruby Shaw"];
const COURIERS = ["Alex R.", "Sam K.", "Dee M.", "Jess W.", "Omar H."];
let picked = [];
function renderPicked() {
  $("o-chips").innerHTML = Object.values(menu).map((m) => {
    const n = picked.filter((p) => p.menu === m.id).reduce((a, p) => a + p.qty, 0);
    return `<button type="button" data-m="${m.id}"><span>${m.emoji}</span>${esc(m.name)}${n ? `<b>${n}</b>` : ""}</button>`;
  }).join("");
  $("o-picked").innerHTML = picked.map((p, i) => `<li><span>${p.qty}× ${menu[p.menu].emoji} ${esc(menu[p.menu].name)}</span>
    ${DONENESS[p.menu] ? `<select data-i="${i}">${DONENESS[p.menu].map((d) => `<option ${d === p.mod ? "selected" : ""}>${d}</option>`).join("")}</select>` : "<span></span>"}
    <button class="link" type="button" data-rm="${i}">Remove</button></li>`).join("");
}
$("o-chips").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const p = picked.find((x) => x.menu === b.dataset.m);
  if (p) p.qty++; else picked.push({ menu: b.dataset.m, qty: 1, mod: DONENESS[b.dataset.m] ? "medium" : null });
  renderPicked();
});
$("o-picked").addEventListener("click", (e) => { const b = e.target.closest("[data-rm]"); if (b) { picked.splice(+b.dataset.rm, 1); renderPicked(); } });
$("o-picked").addEventListener("change", (e) => { if (e.target.dataset.i) picked[+e.target.dataset.i].mod = e.target.value; });
$("o-random").addEventListener("click", () => {
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  $("o-guest").value = pick(NAMES); $("o-source").value = pick(sources);
  $("o-courier").value = ["DoorDash", "Uber Eats", "Grubhub"].includes($("o-source").value) ? pick(COURIERS) : "";
  const ids = Object.keys(menu).sort(() => Math.random() - 0.5).slice(0, 2 + Math.floor(Math.random() * 2));
  picked = ids.map((id) => ({ menu: id, qty: 1, mod: DONENESS[id] ? pick(DONENESS[id]) : null }));
  renderPicked();
});
$("add").addEventListener("click", () => {
  picked = []; $("o-guest").value = ""; $("o-courier").value = ""; renderPicked(); $("dlg").showModal(); $("o-guest").focus();
});
$("orderform").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;
  e.preventDefault();
  if (!picked.length) { toast("Pick at least one item.", true); return; }
  try {
    const r = await post("/orders", { guest: $("o-guest").value, source: $("o-source").value, courier: $("o-courier").value || null,
                                      pickup_in: +$("o-pickup").value, items: picked });
    $("dlg").close(); filter = "open"; setTimeout(() => focusTicket(r.num), 150);
  } catch (err) { toast(err.message, true); }
});

// ------------------------------------------------------------------ boot
(async () => {
  const here = `${location.origin}/k/${KID}/scan`;
  $("nav-tickets").href = `/k/${KID}`; $("nav-scan").href = here; $("open-scan").href = here; $("nav-print").href = `/k/${KID}/print`;
  // a phone can't open localhost: the server says where phones should go instead
  const cfg = await (await fetch("/api/config")).json().catch(() => ({}));
  const phoneUrl = `${cfg.phone_base || location.origin}/k/${KID}/scan`;
  $("qr").src = `/qr.svg?v=2&d=${encodeURIComponent(phoneUrl)}`;
  $("qr").title = phoneUrl;
  if (cfg.lan) $("pair-note").innerHTML = `Phone on the same Wi-Fi as this laptop. It will warn that the connection isn't private: tap <b>Show Details › visit this website</b> (Chrome: Advanced › Proceed).`;
  if (cfg.unreachable) $("pair-note").innerHTML = `<span class="warn">This dashboard is on localhost, which a phone can't open.</span> Start it with <b>run_local.py</b>, or use the live site.`;
  const m = await (await fetch("/api/menu")).json();
  m.menu.forEach((x) => (menu[x.id] = x)); sources = m.sources;
  $("o-source").innerHTML = sources.map((s) => `<option>${s}</option>`).join("");
  connect();
})();
const mountOrbs = () => { orbs.status = HOrbs.mount($("status-orb"), "breathing", 20); orbs.mic = HOrbs.mount($("mic-orb"), "breathing", 20); };
if (window.HOrbs) mountOrbs(); else addEventListener("orbs-ready", mountOrbs);
