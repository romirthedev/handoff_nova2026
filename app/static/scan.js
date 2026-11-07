// Phone pass camera. Learns the empty packing spot, notices when an order slides in and holds still,
// photographs it and asks the server whose order it is and what's missing.
const $ = (id) => document.getElementById(id);
const KID = location.pathname.split("/")[2];
const W = 48, H = 36;                 // motion is judged on a tiny grayscale copy of the frame
const T_PRESENT = 13, T_STILL = 3.2, T_CHANGE = 14, STILL_FRAMES = 4, HOLD_MS = 3000, RECHECK_MS = 4000;

let stream = null, facing = "environment", mode = "off", base = null, prev = null, captured = null;
let still = 0, emptyFrames = 0, shownAt = 0, openTickets = 0, lastSaid = { text: "", at: 0 };
const small = document.createElement("canvas"); small.width = W; small.height = H;
const sctx = small.getContext("2d", { willReadFrequently: true });
const orbs = {};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function toast(text, bad) {
  const t = $("toast"); t.textContent = text; t.className = "toast on" + (bad ? " bad" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), 3500);
}
function hud(text, orbState) { $("hud").textContent = text; if (orbs.hud && orbState) orbs.hud.set(orbState); }
function speak(text, force) {
  if (!$("voice").checked || !("speechSynthesis" in window)) return;
  if (!force && text === lastSaid.text && Date.now() - lastSaid.at < 20000) return;  // don't repeat the same line
  lastSaid = { text, at: Date.now() };
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

// ------------------------------------------------------------------ camera
async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error(location.protocol === "https:" ? "This browser can't open the camera." : "The camera needs an https:// link. Open the deployed site, not a local http address.");
  }
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = await navigator.mediaDevices.getUserMedia({ audio: false,
    video: { facingMode: { ideal: facing }, width: { ideal: 2560 }, height: { ideal: 1920 } } });
  $("video").srcObject = stream;
  await $("video").play().catch(() => {});
  await new Promise((r) => ($("video").videoWidth ? r() : $("video").addEventListener("loadeddata", r, { once: true })));
}

function sample() {
  sctx.drawImage($("video"), 0, 0, W, H);
  const d = sctx.getImageData(0, 0, W, H).data, g = new Float32Array(W * H);
  for (let i = 0; i < g.length; i++) g[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
  return g;
}
const diff = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]); return s / a.length; };

async function learnBase() {
  const frames = [];
  for (let i = 0; i < 6; i++) { frames.push(sample()); await new Promise((r) => setTimeout(r, 80)); }
  base = frames[0].map((_, i) => frames.reduce((a, f) => a + f[i], 0) / frames.length);
  prev = base;
}

function frameJpeg(source, w, h) {
  const scale = Math.min(1, 2400 / Math.max(w, h));  // full detail so the small codes on cards stay readable
  const c = document.createElement("canvas"); c.width = Math.round(w * scale); c.height = Math.round(h * scale);
  c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
  return new Promise((r) => c.toBlob(r, "image/jpeg", 0.88));
}

// ------------------------------------------------------------------ the watch loop
function loop() {
  if (mode === "off" || mode === "busy" || !base) return;
  const f = sample(), dPrev = diff(f, prev), dBase = diff(f, base);
  prev = f;
  $("meter").style.width = `${Math.min(100, (dBase / T_PRESENT) * 50)}%`;
  if (!$("auto").checked && mode !== "result") return;

  if (mode === "watch") {
    if (dBase > T_PRESENT) { mode = "settle"; still = 0; hud("Order in view · hold still", "listening"); }
    else if (dPrev < 2) for (let i = 0; i < f.length; i++) base[i] = base[i] * 0.97 + f[i] * 0.03;  // follow slow light changes
  } else if (mode === "settle") {
    if (dBase < T_PRESENT * 0.6) { mode = "watch"; hud("Watching the pass", "breathing"); return; }
    still = dPrev < T_STILL ? still + 1 : 0;
    if (still >= STILL_FRAMES) check(f);
  } else if (mode === "result") {
    const elapsed = Date.now() - shownAt;
    emptyFrames = dBase < T_PRESENT * 0.6 ? emptyFrames + 1 : 0;
    if (emptyFrames >= 3 && elapsed > HOLD_MS) { hideResult(); mode = "watch"; hud("Watching the pass", "breathing"); return; }
    still = dPrev < T_STILL ? still + 1 : 0;
    // the order changed under the camera (someone added the missing item): check again
    if ($("auto").checked && still >= STILL_FRAMES + 2 && elapsed > RECHECK_MS && captured && diff(f, captured) > T_CHANGE && dBase > T_PRESENT) check(f);
  }
}
setInterval(loop, 150);

async function check(frame) {
  if (mode === "busy") return;
  const before = mode;
  mode = "busy"; captured = frame || null; still = 0;
  $("stage").classList.add("busy");
  hud(`Checking against ${openTickets || "the"} tickets…`, "working");
  try {
    const v = $("video");
    const blob = await frameJpeg(v, v.videoWidth, v.videoHeight);
    await send(blob);
  } catch (e) {
    toast(e.message, true);
    mode = before === "result" ? "result" : "watch";
    hud("Watching the pass", "breathing");
  } finally { $("stage").classList.remove("busy"); }
}

async function send(blob) {
  const r = await fetch(`/api/k/${KID}/scan`, { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
  const j = await r.json().catch(() => ({}));
  if (r.status === 409) { mode = "settle"; return; }
  if (!r.ok) throw new Error(j.detail || "Couldn't check that order.");
  showResult(j);
}

function showResult(r) {
  const el = $("result");
  if (r.empty) {  // nothing recognisable: say so once, no full-screen alarm; it rechecks when the scene changes
    hideResult();
    shownAt = Date.now(); emptyFrames = 0; mode = stream ? "result" : "off";
    hud(r.reason, "breathing");
    $("say").textContent = r.reason;
    speak(r.reason.split(". ")[0] + ".");
    return;
  }
  const first = r.guest ? r.guest.split(" ")[0] : "";
  const verdict = r.ok ? "Good to go" : r.order ? (r.missing.length ? "Missing items" : "Check the order") : "No matching ticket";
  const lines = [
    ...r.missing.map((x) => `<li class="m"><span class="s">✕</span>Missing ${x.qty > 1 ? x.qty + "× " : ""}${x.emoji} ${esc(x.name)}</li>`),
    ...r.extra.map((x) => `<li class="x"><span class="s">+</span>Extra ${x.qty > 1 ? x.qty + "× " : ""}${x.emoji} ${esc(x.name)}</li>`),
    ...r.present.map((x) => `<li class="p"><span class="s">✓</span>${x.qty > 1 ? x.qty + "× " : ""}${x.emoji} ${esc(x.name)}</li>`),
  ].join("");
  el.className = "result on " + (r.ok ? "ok" : "bad");
  el.innerHTML = `<div class="ring"></div>
    <div class="verdict">${r.ok ? "✓" : "✕"} ${verdict}</div>
    <div class="name">${r.order ? esc(r.guest) : "Unknown order"}</div>
    <div class="meta">${r.order ? `#${r.order} · ${esc(r.source)} · ` : ""}${r.seconds}s</div>
    ${r.ok ? `<div class="why">Pack it up.</div>` : `<div class="why">${esc(r.reason)}</div>`}
    ${lines ? `<ul>${lines}</ul>` : ""}`;
  shownAt = Date.now(); emptyFrames = 0; mode = stream ? "result" : "off";
  hud(r.ok ? `#${r.order} ${r.guest} · good to go` : r.reason, "breathing");
  $("say").textContent = r.ok ? `✓ #${r.order} ${r.guest}` : `✕ ${r.order ? `#${r.order} ` : ""}${r.reason}`;
  if (navigator.vibrate) navigator.vibrate(r.ok ? 60 : [90, 60, 90]);
  speak(r.ok ? `${first}, good to go.` : r.order ? `${first}. ${r.reason}` : r.reason);
}
function hideResult() { $("result").className = "result"; }
$("result").addEventListener("click", () => { hideResult(); if (stream) { mode = "watch"; hud("Watching the pass", "breathing"); } });

// ------------------------------------------------------------------ controls
async function keepAwake() { try { if (navigator.wakeLock) await navigator.wakeLock.request("screen"); } catch {} }
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && stream) keepAwake(); });

$("arm").addEventListener("click", async () => {
  speak("Pass camera on.", true);  // speaking inside the tap unlocks audio on iPhone
  $("arm").disabled = true; $("arm").textContent = "Opening camera…";
  try {
    await startCamera();
    $("intro").style.display = "none";
    hud("Learning the empty spot…", "connecting");
    await learnBase();
    mode = "watch"; $("stage").classList.add("armed"); $("snap").disabled = false;
    hud("Watching the pass", "breathing"); keepAwake();
  } catch (e) {
    $("arm").disabled = false; $("arm").textContent = "Ready to identify";
    toast(e.name === "NotAllowedError" ? "Camera permission was denied. Allow it in your browser settings." : e.message, true);
  }
});
$("snap").addEventListener("click", () => { hideResult(); check(stream ? sample() : null); });
$("rebase").addEventListener("click", async () => {
  if (!stream) return;
  hideResult(); mode = "busy"; hud("Learning the empty spot…", "connecting");
  await learnBase(); mode = "watch"; hud("Watching the pass", "breathing"); toast("Learned the empty spot");
});
$("flip").addEventListener("click", async () => {
  if (!stream) return;
  facing = facing === "environment" ? "user" : "environment";
  mode = "busy";
  try { await startCamera(); await learnBase(); } catch (e) { toast(e.message, true); }
  mode = "watch";
});
$("pick").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", async () => {
  const file = $("file").files[0]; $("file").value = "";
  if (!file) return;
  const prevMode = mode; mode = "busy"; hud("Checking the photo…", "working");
  try {
    const bmp = await createImageBitmap(file);
    await send(await frameJpeg(bmp, bmp.width, bmp.height));
  } catch (e) { toast(e.message, true); mode = prevMode === "busy" ? "off" : prevMode; }
});

// ------------------------------------------------------------------ server link
let ws, tries = 0;
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/k/${KID}?role=camera`);
  ws.onopen = () => { tries = 0; $("live").className = "live on"; $("conn").textContent = "Live"; };
  ws.onclose = () => { $("live").className = "live"; $("conn").textContent = "Offline"; setTimeout(connect, Math.min(8000, 500 * 2 ** tries++)); };
  ws.onmessage = (e) => {
    if (e.data === "pong") return;
    const m = JSON.parse(e.data);
    if (m.type === "state") {
      const k = m.kitchen;
      openTickets = Object.values(k.orders).filter((o) => !["picked_up", "cancelled"].includes(o.status)).length;
      $("kname").textContent = k.name;
      if (mode === "off") $("say").textContent = `${k.name} · ${openTickets} open tickets`;
      document.title = `${k.name} · Pass camera`;
    }
  };
}
setInterval(() => ws && ws.readyState === 1 && ws.send("ping"), 25000);
$("nav-tickets").href = `/k/${KID}`;
connect();
const mountOrbs = () => { orbs.status = HOrbs.mount($("status-orb"), "breathing", 20); orbs.hud = HOrbs.mount($("hud-orb"), "breathing", 20); };
if (window.HOrbs) mountOrbs(); else addEventListener("orbs-ready", mountOrbs);
