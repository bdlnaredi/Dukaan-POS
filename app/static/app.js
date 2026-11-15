/* Dukaan counter app. Works offline: bills are numbered and printed on the device, kept in a queue, and sent
 * to the server when it can be reached. The server re-checks every bill and sends it on to Tally. */
"use strict";

// ---------------------------------------------------------------- small helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const E = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rs = (p) => "₹" + GST.rs(p || 0);
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { toast("This device's storage is full. Send waiting bills, then clear old ones.", true); } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* */ } },
};
function newId() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("").replace(/^(.{8})(.{4})/, "$1-$2-");
}
function linkId() {
  const a = new Uint8Array(9);
  crypto.getRandomValues(a);
  return btoa(String.fromCharCode.apply(null, a)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}
function todayISO(d) {
  d = d || new Date();
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}
function nowTime() { const d = new Date(); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((x) => String(x).padStart(2, "0")).join(":"); }
function fyOf(day) {
  const [y, m] = day.split("-").map(Number);
  const s = m >= 4 ? y : y - 1;
  return String(s % 100).padStart(2, "0") + String((s + 1) % 100).padStart(2, "0");
}
function niceDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${d}-${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]}-${y}`;
}
function focusLater(sel) { setTimeout(() => { const el = $(sel); if (el) el.focus(); }, 50); }
let toastTimer;
function toast(msg, bad) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.classList.remove("hide");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hide"), bad ? 5000 : 2600);
}
let audio;
function beep(ok = true) {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const o = audio.createOscillator(), g = audio.createGain();
    o.frequency.value = ok ? 1650 : 300;
    g.gain.value = 0.08;
    o.connect(g); g.connect(audio.destination);
    o.start(); o.stop(audio.currentTime + (ok ? 0.08 : 0.25));
  } catch (e) { /* no sound */ }
}
const ICON = {
  cash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/></svg>',
  upi: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM20 14v7M14 20h3"/></svg>',
  card: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/></svg>',
  split: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v18M5 8h4M5 12h4M15 12h4M15 16h4"/></svg>',
  credit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h12l4 4v12H4z"/><path d="M8 12h8M8 16h5"/></svg>',
  search: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  scan: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7V4h3M21 7V4h-3M3 17v3h3M21 17v3h-3M7 8v8M10 8v8M13 8v8M16 8v8"/></svg>',
};
const MODE = { cash: "Cash", upi: "UPI", card: "Card", credit: "Credit" };

// ---------------------------------------------------------------- state
const S = {
  token: LS.get("dk_token", ""),
  device: LS.get("dk_device", ""),
  boot: null,
  online: navigator.onLine,
  queue: LS.get("dk_queue", []),       // bills not yet accepted by the server
  recent: LS.get("dk_recent", []),     // bills made on this device (for reprint, offline)
  held: LS.get("dk_held", []),
  cart: LS.get("dk_cart", null) || { lines: [], bill_disc: 0, buyer: {} },
  counterId: LS.get("dk_counter", null),
  items: [], byName: {}, byCode: {},
  view: "bill",
};
if (!S.device) { S.device = newId(); LS.set("dk_device", S.device); }
const me = () => (S.boot && S.boot.me) || {};
const isOwner = () => me().role === "owner";
const settings = () => (S.boot && S.boot.settings) || { shop: {}, print: {} };
function counter() { return ((S.boot && S.boot.counters) || []).find((c) => c.id === S.counterId); }

async function api(path, body, raw) {
  let r;
  try {
    r = await fetch(path, {
      method: body !== undefined ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-Dukaan-Token": S.token || "" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    setOnline(false);
    const err = new Error("Can't reach the Dukaan server. Check the connection.");
    err.offline = true;
    throw err;
  }
  setOnline(true);
  if (raw && r.ok) return r;
  const j = await r.json().catch(() => ({ error: `Server error ${r.status}` }));
  if (r.status === 401 && path !== "/api/login") { signedOut(); }
  if (!r.ok) { const err = new Error(j.error || `Error ${r.status}`); err.status = r.status; throw err; }
  return j;
}
function setOnline(v) { if (S.online !== v) { S.online = v; updDot(); } }
function updDot() {
  const d = $("#dot"), t = $("#syncTxt");
  if (!d) return;
  const errs = S.queue.filter((b) => b._error).length;
  d.className = "dot" + (errs ? " bad" : !S.online || S.queue.length ? " off" : "");
  t.textContent = errs ? `${errs} not accepted` : S.queue.length ? `${S.queue.length} to send` : S.online ? "Online" : "Offline";
}

// ---------------------------------------------------------------- start
async function start() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  window.addEventListener("online", () => { setOnline(true); sync(); });
  window.addEventListener("offline", () => setOnline(false));
  window.addEventListener("hashchange", route);
  document.addEventListener("keydown", keys);
  $("#menuBtn").onclick = toggleMenu;
  $("#syncBtn").onclick = syncInfo;
  if (!S.token) return showLogin();
  await loadBoot();
  if (!S.boot) return showLogin();
  afterBoot();
}
async function loadBoot(quiet) {
  try {
    const b = await api("/api/boot");
    S.boot = b;
    LS.set("dk_boot", b);
  } catch (e) {
    if (e.status === 401) return;
    S.boot = S.boot || LS.get("dk_boot", null);
    if (!quiet && S.boot) toast("Working offline. Bills will be sent when the server is back.");
  }
  indexItems();
}
function indexItems() {
  S.items = (S.boot && S.boot.items) || [];
  S.byName = {}; S.byCode = {};
  for (const it of S.items) {
    it._s = (it.name + " " + (it.group || "") + " " + (it.aliases || []).join(" ")).toLowerCase();
    S.byName[it.name] = it;
    for (const c of it.codes || []) S.byCode[c.toLowerCase()] = it;
  }
}
function afterBoot() {
  $("#top").classList.remove("hide");
  const sh = settings().shop || {};
  $("#shopName").textContent = sh.name || "Dukaan";
  document.title = (sh.name || "Dukaan") + " · Dukaan";
  if (!counter() && !(isOwner() && S.counterId === 0)) return chooseCounter();
  $("#who").textContent = counter() ? `${counter().name} · ${me().name}` : `${me().name} · not billing on this device`;
  buildNav();
  route();
  updDot();
  clearInterval(S.t1); clearInterval(S.t2);
  S.t1 = setInterval(sync, 8000);
  S.t2 = setInterval(() => loadBoot(true).then(() => { if (S.view === "bill") renderResults(); }), 180000);
  sync();
}
function buildNav() {
  const v = [["bill", "Billing"], ["bills", "Bills"]];
  if (isOwner()) v.push(["reports", "Reports"], ["items", "Items"], ["labels", "Labels"], ["tally", "Tally"], ["settings", "Settings"]);
  $("#nav").innerHTML = v.map(([k, l]) => `<a href="#${k}" data-v="${k}">${l}</a>`).join("");
  S.navItems = v;
}
function route() {
  if (!S.boot) return;
  let v = (location.hash || "#bill").slice(1);
  if (!["bill", "bills", "reports", "items", "labels", "tally", "settings"].includes(v)) v = "bill";
  if (!isOwner() && !["bill", "bills"].includes(v)) v = "bill";
  S.view = v;
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.v === v));
  closeMenu();
  closeSheet();
  ({ bill: viewBill, bills: viewBills, reports: viewReports, items: viewItems, labels: viewLabels, tally: viewTally, settings: viewSettings })[v]();
}
function toggleMenu() {
  const m = $("#menu");
  if (!m.classList.contains("hide")) return closeMenu();
  const v = S.navItems || [["bill", "Billing"], ["bills", "Bills"]];
  m.innerHTML = v.map(([k, l]) => `<a href="#${k}">${l}</a>`).join("") +
    `<button id="mHeld">Held bills (${S.held.length})</button><button id="mCounter">Change counter</button>` +
    `<button id="mOut">Sign out</button><div class="small mute" style="padding:8px 12px">Dukaan ${E((S.boot || {}).version || "")}</div>`;
  m.classList.remove("hide");
  $("#mOut").onclick = signOut;
  $("#mCounter").onclick = () => { closeMenu(); chooseCounter(); };
  $("#mHeld").onclick = () => { closeMenu(); heldSheet(); };
  setTimeout(() => document.addEventListener("click", closeMenuOutside), 0);
}
function closeMenuOutside(e) { if (!$("#menu").contains(e.target) && e.target !== $("#menuBtn")) closeMenu(); }
function closeMenu() { $("#menu").classList.add("hide"); document.removeEventListener("click", closeMenuOutside); }

// ---------------------------------------------------------------- sign in
async function showLogin() {
  $("#top").classList.add("hide");
  const app = $("#app");
  app.innerHTML = `<div id="login" class="card"><img class="logo" src="/icon.svg" alt=""><h2 style="text-align:center" id="lgShop">Dukaan</h2>
    <div class="row" style="justify-content:center;margin-bottom:12px"><div class="seg"><button class="on" data-k="cashier">Cashier</button><button data-k="owner">Owner</button></div></div>
    <div id="lgBody"></div><div id="lgErr"></div></div>`;
  let hello = { cashiers: [] };
  try { hello = await api("/api/hello"); } catch (e) { $("#lgErr").innerHTML = `<div class="note bad">${E(e.message)}</div>`; }
  $("#lgShop").textContent = hello.shop || "Dukaan";
  const seg = $$("#login .seg button");
  seg.forEach((b) => (b.onclick = () => { seg.forEach((x) => x.classList.toggle("on", x === b)); body(b.dataset.k); }));
  const body = (k) => {
    const el = $("#lgBody");
    if (k === "owner" || !hello.cashiers.length) {
      if (!hello.cashiers.length) { seg[1].classList.add("on"); seg[0].classList.remove("on"); }
      el.innerHTML = `<label class="f"><span>Owner password</span><input class="in" type="password" id="lgPw" autocomplete="current-password"></label>
        <button class="btn pri block big" style="margin-top:14px" id="lgGo">Sign in</button>
        ${hello.cashiers.length ? "" : '<p class="small mute">No cashiers yet. Sign in as the owner to add them in Settings.</p>'}`;
      const go = () => login({ kind: "owner", password: $("#lgPw").value });
      $("#lgGo").onclick = go;
      $("#lgPw").onkeydown = (e) => { if (e.key === "Enter") go(); };
      $("#lgPw").focus();
    } else {
      el.innerHTML = `<div class="names">${hello.cashiers.map((n) => `<button class="btn" data-n="${E(n)}">${E(n)}</button>`).join("")}</div>`;
      $$(".names button", el).forEach((b) => (b.onclick = () => pinPad(b.dataset.n)));
    }
  };
  body("cashier");
}
function pinPad(name) {
  let pin = "";
  const el = $("#lgBody");
  const draw = () => {
    el.innerHTML = `<div style="text-align:center;font-weight:700;font-size:18px">${E(name)}</div>
      <div class="pin">${Array.from({ length: Math.max(4, pin.length) }, (_, i) => `<i class="${i < pin.length ? "on" : ""}"></i>`).join("")}</div>
      <div class="keypad">${[1, 2, 3, 4, 5, 6, 7, 8, 9, "⌫", 0, "OK"].map((k) => `<button data-k="${k}">${k}</button>`).join("")}</div>
      <button class="btn ghost block" style="margin-top:8px" id="pinBack">Someone else</button>`;
    $$(".keypad button", el).forEach((b) => (b.onclick = () => press(b.dataset.k)));
    $("#pinBack").onclick = () => showLogin();
  };
  const press = (k) => {
    if (k === "⌫") pin = pin.slice(0, -1);
    else if (k === "OK") return pin.length >= 4 && login({ kind: "cashier", name, pin });
    else if (pin.length < 8) pin += k;
    draw();
    if (pin.length >= 4 && pin.length === 8) login({ kind: "cashier", name, pin });
  };
  S.pinKeys = (e) => { if (/^\d$/.test(e.key)) press(e.key); else if (e.key === "Backspace") press("⌫"); else if (e.key === "Enter") press("OK"); };
  draw();
}
async function login(d) {
  try {
    const r = await api("/api/login", { ...d, device: S.device });
    S.token = r.token;
    LS.set("dk_token", r.token);
    S.pinKeys = null;
    await loadBoot();
    if (!S.boot) throw new Error("Signed in, but couldn't load the shop's items.");
    afterBoot();
  } catch (e) {
    $("#lgErr").innerHTML = `<div class="note bad">${E(e.message)}</div>`;
    if (d.kind === "cashier") setTimeout(() => pinPad(d.name), 50);
  }
}
function signedOut() { S.token = ""; LS.del("dk_token"); }
async function signOut() {
  closeMenu();
  if (S.queue.length && !confirm(`${S.queue.length} bill(s) haven't reached the server yet. They stay on this device and are sent after the next sign in. Sign out?`)) return;
  try { await api("/api/logout", {}); } catch (e) { /* offline */ }
  signedOut();
  S.boot = null;
  location.hash = "";
  showLogin();
}
function chooseCounter() {
  const cs = (S.boot && S.boot.counters) || [];
  sheet("Which counter is this?", `<p class="mute small">Each counter has its own bill number series. Pick the one this device bills from.</p>
    <div class="list">${cs.map((c) => `<div class="it" data-id="${c.id}"><div class="grow"><b>${E(c.name)}</b><div class="small mute">Bills ${E(settings().prefix || "")}${E(c.series)}/…${c.device && c.device !== S.device ? " · in use on another device" : ""}</div></div><span class="chip">${c.id === S.counterId ? "This device" : "Use"}</span></div>`).join("") || '<div class="note">No counters yet. The owner adds them in Settings.</div>'}
    ${isOwner() ? '<div class="it" data-id="0"><div class="grow"><b>Don\'t bill on this device</b><div class="small mute">Only see reports and manage the shop from here</div></div></div>' : ""}</div>`, "", { noClose: !counter() && S.counterId !== 0 });
  $$(".sheet .it").forEach((el) => (el.onclick = async () => {
    const id = Number(el.dataset.id);
    if (id === 0) { S.counterId = 0; LS.set("dk_counter", 0); closeSheet(); return afterBoot(); }
    const c = cs.find((x) => x.id === id);
    if (c.device && c.device !== S.device && !confirm(`${c.name} is being used on another device. Move it to this one? Bills from the other device will then be marked for the owner to check.`)) return;
    try {
      const r = await api("/api/counter/claim", { counter_id: id });
      S.boot.numbers[id] = r.numbers;
      c.device = S.device;
      LS.set("dk_boot", S.boot);
    } catch (e) {
      if (!e.offline) return toast(e.message, true);
      toast("Offline: using this counter on this device. Connect soon so the server knows.");
    }
    S.counterId = id;
    LS.set("dk_counter", id);
    closeSheet();
    afterBoot();
  }));
}

// ---------------------------------------------------------------- sheets
function sheet(title, body, foot, opts = {}) {
  closeSheet();
  const v = document.createElement("div");
  v.id = "veil";
  const s = document.createElement("div");
  s.className = "sheet" + (opts.wide ? " wide" : "");
  s.innerHTML = `<div class="sh-h"><h2>${title}</h2>${opts.noClose ? "" : '<button class="x" aria-label="Close">✕</button>'}</div><div class="sh-b">${body}</div>${foot ? `<div class="sh-f">${foot}</div>` : ""}`;
  document.body.append(v, s);
  if (!opts.noClose) { v.onclick = closeSheet; $(".x", s).onclick = closeSheet; }
  S.sheetClose = opts.onclose;
  return s;
}
function closeSheet() {
  const s = $(".sheet");
  if (!s) return;
  s.remove();
  $("#veil") && $("#veil").remove();
  const f = S.sheetClose;
  S.sheetClose = null;
  if (f) f();
}

// ---------------------------------------------------------------- billing
function saveCart() { LS.set("dk_cart", S.cart); }
function viewBill() {
  if (!counter()) {
    $("#app").innerHTML = `<div class="card" style="max-width:520px;margin:20px auto"><h2>Not billing on this device</h2><p class="mute">Pick a counter to make bills here.</p><button class="btn pri" id="pickCtr">Pick a counter</button></div>`;
    $("#pickCtr").onclick = chooseCounter;
    return;
  }
  $("#app").innerHTML = `<div id="bill">
    <section>
      <div class="search card" style="padding:0"><span class="ic">${ICON.search}</span>
        <input class="in" id="q" placeholder="Search item or scan barcode" autocomplete="off" enterkeyhint="search" style="border:0">
        <button class="btn ghost scan" id="camBtn" title="Scan with camera">${ICON.scan}</button></div>
      <div id="results" class="card hide" style="padding:0"></div>
      <div id="favs"></div>
    </section>
    <section class="card cart">
      <div class="cart-h"><b class="grow">Bill <span class="mute small" id="nextNo"></span></b>
        <button class="btn sm" id="holdBtn" title="Keep this bill aside (F3)">Hold</button>
        <button class="btn sm bad" id="clearBtn">Clear</button></div>
      <div class="lines" id="lines"></div>
      <div class="cust" id="custRow"></div>
      <div class="tot" id="tot"></div>
      <div class="cart-f"><button class="btn" id="discBtn">Discount</button><button class="btn" id="custBtn">Customer</button>
        <button class="btn pri big" id="payBtn">Pay (F8)</button></div>
    </section>
  </div>
  <div id="paybar"><div class="grow"><div class="small mute" id="pbCount"></div><div class="gt" id="pbTotal"></div></div>
    <button class="btn" id="pbCust" aria-label="Customer">👤</button><button class="btn pri big" id="pbPay">Pay</button></div>`;
  const q = $("#q");
  q.oninput = renderResults;
  q.onkeydown = searchKeys;
  $("#camBtn").onclick = camera;
  $("#holdBtn").onclick = holdBill;
  $("#clearBtn").onclick = () => { if (!S.cart.lines.length || confirm("Clear this bill?")) { newCart(); } };
  $("#discBtn").onclick = billDiscount;
  $("#custBtn").onclick = $("#pbCust").onclick = customerSheet;
  $("#payBtn").onclick = $("#pbPay").onclick = paySheet;
  renderResults();
  renderCart();
  if (matchMedia("(min-width: 900px)").matches) q.focus();
}
function newCart() { S.cart = { lines: [], bill_disc: 0, buyer: {} }; saveCart(); renderCart(); }
function searchItems(text) {
  const t = text.trim().toLowerCase();
  if (!t) return [];
  const words = t.split(/\s+/);
  const out = [];
  for (const it of S.items) {
    if (words.every((w) => it._s.includes(w))) out.push(it);
  }
  out.sort((a, b) => (b.name.toLowerCase().startsWith(t) - a.name.toLowerCase().startsWith(t)) || (b.can_sell - a.can_sell) || a.name.localeCompare(b.name));
  return out.slice(0, 40);
}
function renderResults() {
  const q = $("#q");
  if (!q) return;
  const t = q.value;
  const box = $("#results");
  const favs = $("#favs");
  if (!t.trim()) {
    box.classList.add("hide");
    const fv = S.items.filter((i) => i.fav && i.can_sell);
    favs.innerHTML = fv.length ? `<div class="favs">${fv.map((i) => `<button class="fav" data-n="${E(i.name)}"><b>${E(i.name)}</b><span>${rs(i.price)}${i.loose ? " / " + E(i.unit) : ""}</span></button>`).join("")}</div>`
      : `<div class="card mute small" style="margin-top:10px">Type a few letters of an item, or scan its barcode. ${isOwner() ? "Mark items as favourites on the Items page to get quick buttons here." : ""}</div>`;
    $$(".fav", favs).forEach((b) => (b.onclick = () => addItem(S.byName[b.dataset.n])));
    return;
  }
  favs.innerHTML = "";
  const code = S.byCode[t.trim().toLowerCase()];
  const res = code ? [code] : searchItems(t);
  S.res = res; S.sel = 0;
  box.classList.remove("hide");
  box.innerHTML = res.length ? res.map((i, k) => `<div class="res${k === 0 ? " sel" : ""}${i.can_sell ? "" : " no"}" data-k="${k}">
      <div class="grow"><div class="nm">${E(i.name)}</div><div class="small mute">${i.mrp ? "MRP " + rs(i.mrp) + " · " : ""}GST ${i.gst == null ? "?" : i.gst + "%"} · stock ${i.stock == null ? "-" : +i.stock.toFixed(3)} ${E(i.unit)}${i.can_sell ? "" : ' · <span style="color:var(--bad)">' + E(i.issues[0] || "can't sell") + "</span>"}</div></div>
      <b class="num">${rs(i.price)}${i.loose ? '<span class="small mute">/' + E(i.unit) + "</span>" : ""}</b></div>`).join("")
    : `<div class="empty">No item matches “${E(t)}”.</div>`;
  $$(".res", box).forEach((el) => (el.onclick = () => { addItem(res[el.dataset.k]); }));
}
function searchKeys(e) {
  const res = S.res || [];
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    S.sel = Math.max(0, Math.min(res.length - 1, S.sel + (e.key === "ArrowDown" ? 1 : -1)));
    $$(".res").forEach((el, k) => el.classList.toggle("sel", k === S.sel));
    const el = $$(".res")[S.sel];
    el && el.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") {
    e.preventDefault();
    const t = e.target.value.trim();
    if (!t && S.cart.lines.length) return paySheet();
    const code = S.byCode[t.toLowerCase()];
    if (code) return addItem(code);
    if (res[S.sel]) addItem(res[S.sel]);
    else if (t) { beep(false); toast(`No item with barcode or name “${t}”.`, true); e.target.select(); }
  } else if (e.key === "Escape") { e.target.value = ""; renderResults(); }
}
function addItem(it, qty) {
  if (!it) return;
  if (!it.can_sell) {
    beep(false);
    return toast(`${it.name} can't be sold yet: ${it.issues.join(", ")}.${isOwner() ? " Fix it in Tally or on the Items page." : " Ask the owner."}`, true);
  }
  const q = $("#q");
  if (q) { q.value = ""; renderResults(); }
  if (it.loose && qty == null) return qtySheet(it);
  const L = S.cart.lines;
  let line = L.find((l) => l.item === it.name && l.price === it.price && !l.disc);
  if (line && !it.loose) {
    line.qty = String(GST.qty(GST.milli(line.qty) + GST.milli(qty || "1")));
  } else {
    line = { key: newId(), item: it.name, qty: String(qty || "1"), price: it.price, mrp: it.mrp, gst: it.gst, hsn: it.hsn, unit: it.unit, decimals: it.decimals, disc: 0 };
    L.push(line);
  }
  beep(true);
  saveCart();
  renderCart(line.key);
  if (q && matchMedia("(min-width: 900px)").matches) q.focus();
}
function qtySheet(it, line) {
  const unit = it ? it.unit : line.unit;
  const name = it ? it.name : line.item;
  const quick = /kg|kgs|kilo/i.test(unit) ? ["0.1", "0.25", "0.5", "1", "2", "5"] : /^(l|ltr|litre)$/i.test(unit) ? ["0.5", "1", "2", "5"] : ["1", "2", "3", "5", "10"];
  const s = sheet(E(name), `<label class="f"><span>Quantity (${E(unit)})</span><input class="in" id="qq" inputmode="decimal" value="${E(line ? line.qty : "")}" style="font-size:24px;font-weight:700"></label>
    <div class="quick">${quick.map((x) => `<button class="btn" data-q="${x}">${x} ${E(unit)}</button>`).join("")}</div>
    <label class="f"><span>Or the amount (₹)</span><input class="in" id="qa" inputmode="decimal" placeholder="e.g. 50"></label>
    <div class="small mute" id="qInfo" style="margin-top:8px"></div>`,
  `<button class="btn" id="qCancel">Cancel</button><button class="btn pri" id="qOk">${line ? "Update" : "Add"}</button>`);
  const price = it ? it.price : line.price;
  const dec = it ? it.decimals : line.decimals;
  const info = () => {
    try { $("#qInfo").textContent = `${$("#qq").value} ${unit} × ${rs(price)} = ${rs(GST.roundDiv(price * GST.milli($("#qq").value), 1000))}`; } catch (e) { $("#qInfo").textContent = ""; }
  };
  $("#qq").oninput = () => { $("#qa").value = ""; info(); };
  $("#qa").oninput = () => {
    const p = GST.paise($("#qa").value);
    if (p > 0) { const qm = Math.max(1, Math.round((p * 1000) / price)); $("#qq").value = GST.qty(qm); info(); }
  };
  $$(".quick button", s).forEach((b) => (b.onclick = () => { $("#qq").value = b.dataset.q; info(); }));
  const ok = () => {
    const v = $("#qq").value.trim();
    let qm;
    try { qm = GST.milli(v); } catch (e) { return toast(e.message, true); }
    if (qm <= 0) return toast("Enter the quantity.", true);
    if (qm % Math.pow(10, 3 - Math.min(3, dec || 0))) return toast(`${unit} can't have more than ${dec || 0} decimals.`, true);
    closeSheet();
    if (line) { line.qty = GST.qty(qm); saveCart(); renderCart(line.key); } else addItem(it, GST.qty(qm));
  };
  $("#qOk").onclick = ok;
  $("#qq").onkeydown = $("#qa").onkeydown = (e) => { if (e.key === "Enter") ok(); };
  $("#qCancel").onclick = closeSheet;
  info();
  focusLater("#qq");
}
function calcCart(extra) {
  try {
    const c = GST.compute({ lines: S.cart.lines, bill_disc: S.cart.bill_disc || 0, round: settings().round !== false, buyer: S.cart.buyer || {}, ...(extra || {}) });
    return c;
  } catch (e) { return { error: e.message }; }
}
function nextNumberPreview() {
  const c = counter();
  if (!c) return "";
  const fy = fyOf(todayISO());
  const n = Math.max(LS.get("dk_nums", {})[`${c.id}:${fy}`] || 0, ((S.boot.numbers || {})[c.id] || {})[fy] || 0) + 1;
  return `${settings().prefix || ""}${c.series}/${fy}/${n}`;
}
function renderCart(flashKey) {
  const L = S.cart.lines;
  const box = $("#lines");
  if (!box) return;
  $("#nextNo").textContent = nextNumberPreview();
  box.innerHTML = L.length ? L.map((l) => {
    const gross = (() => { try { return GST.roundDiv(l.price * GST.milli(l.qty), 1000); } catch (e) { return 0; } })();
    return `<div class="ln${l.key === flashKey ? " flash" : ""}" data-k="${l.key}">
      <div class="nm" data-edit>${E(l.item)}</div>
      <div class="qty">${l.decimals ? `<button data-edit>✎</button><input value="${E(l.qty)}" readonly data-edit>` : `<button data-d="-1">−</button><input value="${E(l.qty)}" inputmode="numeric" data-q><button data-d="1">+</button>`}</div>
      <div class="amt num">${rs(gross - (l.disc || 0))}</div>
      <div class="meta">${rs(l.price)}${l.decimals ? "/" + E(l.unit) : ""}${l.mrp && l.mrp > l.price ? ` · MRP ${rs(l.mrp)}` : ""} · GST ${l.gst}%${l.disc ? ` · <span style="color:var(--ok)">−${rs(l.disc)}</span>` : ""}</div>
    </div>`;
  }).join("") : `<div class="empty">No items yet.<br><span class="small">Search or scan to add.</span></div>`;
  $$(".ln", box).forEach((el) => {
    const l = L.find((x) => x.key === el.dataset.k);
    $$("[data-d]", el).forEach((b) => (b.onclick = () => {
      const qm = GST.milli(l.qty) + Number(b.dataset.d) * 1000;
      if (qm <= 0) { S.cart.lines = L.filter((x) => x !== l); } else l.qty = GST.qty(qm);
      saveCart(); renderCart();
    }));
    const qi = $("[data-q]", el);
    if (qi) qi.onchange = () => {
      const v = qi.value.trim();
      if (!/^\d+$/.test(v)) { toast(`${l.unit || "This item"} is counted in whole numbers.`, true); return renderCart(); }
      if (+v === 0) S.cart.lines = L.filter((x) => x !== l); else l.qty = String(+v);
      saveCart(); renderCart();
    };
    $$("[data-edit]", el).forEach((x) => (x.onclick = () => lineSheet(l)));
  });
  const c = calcCart();
  const cust = S.cart.buyer || {};
  const custTxt = cust.ledger ? `<b>${E(cust.ledger)}</b>${cust.gstin ? ' <span class="chip ok">GSTIN</span>' : ""}` : cust.name || cust.phone ? `<b>${E(cust.name || "")}</b> <span class="mute">${E(cust.phone || "")}</span>${cust.gstin ? ' <span class="chip ok">GSTIN</span>' : ""}` : '<span class="mute">Walk-in customer</span>';
  $("#custRow").innerHTML = `<span class="grow ell">${custTxt}</span>${cust.ledger || cust.name || cust.phone ? '<button class="btn sm ghost" id="custX">✕</button>' : ""}`;
  $("#custRow").onclick = (e) => { if (e.target.id === "custX") { S.cart.buyer = {}; saveCart(); renderCart(); } else customerSheet(); };
  const qtyCount = L.length;
  if (c.error && L.length) {
    $("#tot").innerHTML = `<div class="note bad">${E(c.error)}</div>`;
  } else if (!L.length) {
    $("#tot").innerHTML = `<div class="t grand"><span>Total</span><span class="num">${rs(0)}</span></div>`;
  } else {
    const gross = c.lines.reduce((a, l) => a + l.gross, 0);
    const disc = c.lines.reduce((a, l) => a + l.disc, 0);
    $("#tot").innerHTML = `
      <div class="t"><span>${qtyCount} item${qtyCount > 1 ? "s" : ""}</span><span class="num">${rs(gross)}</span></div>
      ${disc ? `<div class="t save"><span>Discount</span><span class="num">−${rs(disc)}</span></div>` : ""}
      <div class="t"><span>Taxable ${rs(c.taxable)} + GST</span><span class="num">${rs(c.cgst + c.sgst)}</span></div>
      ${c.round_off ? `<div class="t"><span>Round off</span><span class="num">${c.round_off > 0 ? "+" : "−"}${rs(Math.abs(c.round_off))}</span></div>` : ""}
      <div class="t grand"><span>Total</span><span class="num">${rs(c.amount)}</span></div>
      ${c.savings ? `<div class="t save small"><span>Customer saves on MRP</span><span class="num">${rs(c.savings)}</span></div>` : ""}`;
  }
  $("#pbCount").textContent = `${qtyCount} item${qtyCount === 1 ? "" : "s"}`;
  $("#pbTotal").textContent = rs(c.error ? 0 : c.amount || 0);
  $("#payBtn").disabled = $("#pbPay").disabled = !L.length;
  const fl = flashKey && $(`.ln[data-k="${flashKey}"]`);
  if (fl) fl.scrollIntoView({ block: "nearest" });
}
function lineSheet(l) {
  const it = S.byName[l.item] || {};
  const canPrice = me().can_discount;
  const s = sheet(E(l.item), `
    <div class="grid2"><label class="f"><span>Quantity (${E(l.unit)})</span><input class="in" id="lq" inputmode="decimal" value="${E(l.qty)}"></label>
    <label class="f"><span>Price per ${E(l.unit || "unit")} (₹, GST included)</span><input class="in" id="lp" inputmode="decimal" value="${(l.price / 100).toFixed(2)}" ${canPrice ? "" : "disabled"}></label></div>
    <div class="grid2"><label class="f"><span>Discount (₹ off this line)</span><input class="in" id="ld" inputmode="decimal" value="${l.disc ? (l.disc / 100).toFixed(2) : ""}" ${canPrice ? "" : "disabled"}></label>
    <label class="f"><span>or discount %</span><input class="in" id="ldp" inputmode="decimal" ${canPrice ? "" : "disabled"}></label></div>
    <div class="small mute" style="margin-top:10px">HSN ${E(l.hsn)} · GST ${l.gst}%${l.mrp ? " · MRP " + rs(l.mrp) : ""}${it.price && it.price !== l.price ? " · usual price " + rs(it.price) : ""}</div>
    ${canPrice ? "" : '<div class="note">Only the owner, or a cashier allowed to, can change prices and give discounts.</div>'}`,
  `<button class="btn bad" id="lRm">Remove</button><button class="btn pri" id="lOk">Done</button>`);
  $("#ldp").oninput = () => {
    const pc = parseFloat($("#ldp").value);
    try {
      const gross = GST.roundDiv(GST.paise($("#lp").value) * GST.milli($("#lq").value), 1000);
      $("#ld").value = pc > 0 ? (Math.round((gross * pc) / 100) / 100).toFixed(2) : "";
    } catch (e) { /* */ }
  };
  $("#lRm").onclick = () => { S.cart.lines = S.cart.lines.filter((x) => x !== l); saveCart(); closeSheet(); renderCart(); };
  const ok = () => {
    const price = GST.paise($("#lp").value), disc = GST.paise($("#ld").value);
    let qm;
    try { qm = GST.milli($("#lq").value); } catch (e) { return toast(e.message, true); }
    if (!(price > 0)) return toast("Enter the price.", true);
    if (isNaN(disc)) return toast("The discount isn't a number.", true);
    if (l.mrp && price > l.mrp) return toast(`Can't sell above the MRP (${rs(l.mrp)}).`, true);
    if (qm % Math.pow(10, 3 - Math.min(3, l.decimals || 0))) return toast(`${l.unit} can't have more than ${l.decimals || 0} decimals.`, true);
    const test = { ...l, qty: GST.qty(qm), price, disc };
    try { GST.compute({ lines: [test], round: false }); } catch (e) { return toast(e.message, true); }
    Object.assign(l, test);
    saveCart(); closeSheet(); renderCart(l.key);
  };
  $("#lOk").onclick = ok;
  $$("input", s).forEach((i) => (i.onkeydown = (e) => { if (e.key === "Enter") ok(); }));
}
function billDiscount() {
  if (!S.cart.lines.length) return;
  if (!me().can_discount) return toast("You aren't allowed to give discounts. Ask the owner.", true);
  const c = calcCart({ bill_disc: 0 });
  if (c.error) return toast(c.error, true);
  sheet("Discount on the whole bill", `<p class="small mute">Bill before discount: <b>${rs(c.total)}</b>. The discount is shared over the items, and GST is worked out on the lower amounts.</p>
    <div class="grid2"><label class="f"><span>Amount off (₹)</span><input class="in" id="bd" inputmode="decimal" value="${S.cart.bill_disc ? (S.cart.bill_disc / 100).toFixed(2) : ""}"></label>
    <label class="f"><span>or % off</span><input class="in" id="bdp" inputmode="decimal"></label></div>`,
  `<button class="btn" id="bdX">No discount</button><button class="btn pri" id="bdOk">Apply</button>`);
  $("#bdp").oninput = () => { const p = parseFloat($("#bdp").value); $("#bd").value = p > 0 ? (Math.round((c.total * p) / 100) / 100).toFixed(2) : ""; };
  $("#bdX").onclick = () => { S.cart.bill_disc = 0; saveCart(); closeSheet(); renderCart(); };
  $("#bdOk").onclick = () => {
    const d = GST.paise($("#bd").value);
    if (isNaN(d)) return toast("That isn't an amount.", true);
    const t = calcCart({ bill_disc: d });
    if (t.error) return toast(t.error, true);
    S.cart.bill_disc = d; saveCart(); closeSheet(); renderCart();
  };
  focusLater("#bd");
}
function customerSheet() {
  const b = S.cart.buyer || {};
  const cust = (S.boot && S.boot.customers) || [];
  const s = sheet("Customer", `
    <div class="seg" style="margin-bottom:10px"><button class="${b.ledger ? "" : "on"}" data-t="walk">Walk-in</button><button class="${b.ledger ? "on" : ""}" data-t="tally">Account in Tally</button></div>
    <div id="cWalk" class="${b.ledger ? "hide" : ""}">
      <div class="grid2"><label class="f"><span>Mobile number</span><input class="in" id="cPh" inputmode="tel" value="${E(b.phone)}" placeholder="for WhatsApp bill"></label>
      <label class="f"><span>Name</span><input class="in" id="cNm" value="${E(b.name)}"></label></div>
      <label class="f"><span>Address</span><textarea class="in" id="cAd" rows="2">${E(b.address)}</textarea></label>
      <label class="f"><span>GSTIN (only for a business that wants GST credit)</span><input class="in" id="cG" value="${E(b.gstin)}" style="text-transform:uppercase" maxlength="15"></label>
      <div class="small mute">For bills of ₹50,000 or more, name and address are required (GST rule 46).</div>
    </div>
    <div id="cTally" class="${b.ledger ? "" : "hide"}">
      <input class="in" id="cQ" placeholder="Search customers in Tally">
      <div class="list" id="cList" style="max-height:45vh;overflow:auto"></div>
      <label class="f"><span>Mobile number (for WhatsApp)</span><input class="in" id="cPh2" inputmode="tel" value="${E(b.phone)}"></label>
    </div>`, `<button class="btn" id="cX">Walk-in, no details</button><button class="btn pri" id="cOk">Done</button>`);
  let mode = b.ledger ? "tally" : "walk", picked = b.ledger ? cust.find((c) => c.name === b.ledger) : null;
  $$(".seg button", s).forEach((x) => (x.onclick = () => {
    mode = x.dataset.t;
    $$(".seg button", s).forEach((y) => y.classList.toggle("on", y === x));
    $("#cWalk").classList.toggle("hide", mode !== "walk");
    $("#cTally").classList.toggle("hide", mode !== "tally");
    if (mode === "tally") $("#cQ").focus();
  }));
  const list = () => {
    const t = $("#cQ").value.toLowerCase();
    const r = cust.filter((c) => !t || (c.name + " " + c.mobile + " " + c.gstin).toLowerCase().includes(t)).slice(0, 60);
    $("#cList").innerHTML = r.map((c) => `<div class="it" data-n="${E(c.name)}"><div class="grow"><b>${E(c.name)}</b><div class="small mute">${E(c.mobile)} ${c.gstin ? "· GSTIN " + E(c.gstin) : ""}</div></div>${c.due > 0.5 ? `<span class="chip warn">due ₹${GST.rs(Math.round(c.due * 100))}</span>` : ""}${picked && picked.name === c.name ? '<span class="chip ok">✓</span>' : ""}</div>`).join("") || '<div class="empty">No customer found in Tally.</div>';
    $$("#cList .it").forEach((el) => (el.onclick = () => { picked = cust.find((c) => c.name === el.dataset.n); if (picked.mobile && !$("#cPh2").value) $("#cPh2").value = picked.mobile; list(); }));
  };
  $("#cQ").oninput = list;
  list();
  $("#cX").onclick = () => { S.cart.buyer = {}; saveCart(); closeSheet(); renderCart(); };
  $("#cOk").onclick = () => {
    if (mode === "tally") {
      if (!picked) return toast("Pick a customer from the list.", true);
      S.cart.buyer = { ledger: picked.name, name: picked.name, gstin: picked.gstin || "", phone: $("#cPh2").value.trim() };
    } else {
      const g = $("#cG").value.trim().toUpperCase();
      if (g && !GST.gstinOk(g)) return toast("That GSTIN isn't right. Check it.", true);
      const ph = $("#cPh").value.replace(/\D/g, "");
      if (ph && !/^(91)?[6-9]\d{9}$/.test(ph)) return toast("That isn't a 10-digit mobile number.", true);
      S.cart.buyer = { name: $("#cNm").value.trim(), phone: ph.slice(-10), address: $("#cAd").value.trim(), gstin: g };
      if (g && !S.cart.buyer.name) return toast("Enter the business name for a GSTIN bill.", true);
    }
    saveCart(); closeSheet(); renderCart();
  };
  if (mode === "walk") focusLater("#cPh");
}
function holdBill() {
  if (!S.cart.lines.length) return heldSheet();
  S.held.unshift({ id: newId(), at: Date.now(), cart: S.cart, by: me().name });
  S.held = S.held.slice(0, 30);
  LS.set("dk_held", S.held);
  newCart();
  toast("Bill kept aside. Open it again from Held bills.");
}
function heldSheet() {
  if (!S.held.length) return toast("No bills are on hold.");
  sheet("Held bills", `<div class="list">${S.held.map((h) => {
    const c = (() => { try { return GST.compute({ lines: h.cart.lines, bill_disc: h.cart.bill_disc, round: true, buyer: {} }); } catch (e) { return { amount: 0 }; } })();
    return `<div class="it" data-id="${h.id}"><div class="grow"><b>${h.cart.lines.length} items · ${rs(c.amount)}</b><div class="small mute">${new Date(h.at).toLocaleTimeString()} · ${E(h.by || "")} ${E((h.cart.buyer || {}).name || (h.cart.buyer || {}).ledger || "")}</div></div><button class="btn sm" data-del="${h.id}">✕</button></div>`;
  }).join("")}</div>`);
  $$(".sheet .it").forEach((el) => (el.onclick = (e) => {
    const id = el.dataset.id;
    if (e.target.dataset.del) { S.held = S.held.filter((h) => h.id !== id); LS.set("dk_held", S.held); closeSheet(); return S.held.length && heldSheet(); }
    const h = S.held.find((x) => x.id === id);
    S.held = S.held.filter((x) => x.id !== id);
    if (S.cart.lines.length) S.held.unshift({ id: newId(), at: Date.now(), cart: S.cart, by: me().name });
    LS.set("dk_held", S.held);
    S.cart = h.cart; saveCart(); closeSheet();
    if (S.view !== "bill") location.hash = "#bill"; else renderCart();
  }));
}

// ---------------------------------------------------------------- paying
function paySheet() {
  if (!S.cart.lines.length) return;
  const c = calcCart();
  if (c.error) return toast(c.error, true);
  const total = c.amount;
  const st = settings();
  const b = S.cart.buyer || {};
  let mode = "cash";
  const s = sheet("Payment", `<div class="paytot"><div class="small mute">To pay</div><div class="a num">${rs(total)}</div>
      <div class="small mute">${c.title}${c.b2b ? " · B2B (GSTIN " + E(b.gstin) + ")" : ""}</div></div>
    <div class="modes">${["cash", "upi", "card", "split", "credit"].map((m) => `<button data-m="${m}" class="${m === mode ? "on" : ""}">${ICON[m]}${m === "split" ? "Split" : MODE[m]}</button>`).join("")}</div>
    <div id="payBody" style="margin-top:12px"></div>`,
  `<button class="btn" id="pBack">Back</button><button class="btn pri big" id="pOk">Done · print</button>`, { wide: false });
  const body = $("#payBody");
  const draw = () => {
    $$(".modes button", s).forEach((x) => x.classList.toggle("on", x.dataset.m === mode));
    if (mode === "cash") {
      const notes = [total, ...[1000, 5000, 10000, 20000, 50000, 200000].map((n) => Math.ceil(total / n) * n)].filter((v, i, a) => a.indexOf(v) === i).slice(0, 5);
      body.innerHTML = `<label class="f"><span>Cash received (₹)</span><input class="in" id="tend" inputmode="decimal" style="font-size:24px;font-weight:700" placeholder="${(total / 100).toFixed(2)}"></label>
        <div class="quick">${notes.map((n) => `<button class="btn" data-v="${n}">${rs(n)}</button>`).join("")}</div><div class="change" id="chg">Exact amount</div>`;
      const upd = () => {
        const t = GST.paise($("#tend").value);
        $("#chg").textContent = !$("#tend").value ? "Exact amount" : isNaN(t) ? "That isn't an amount" : t < total ? `Short by ${rs(total - t)}` : `Give back ${rs(t - total)}`;
        $("#chg").style.background = t < total && $("#tend").value ? "var(--bad-soft)" : "";
      };
      $("#tend").oninput = upd;
      $("#tend").onkeydown = (e) => { if (e.key === "Enter") done(); };
      $$(".quick button", body).forEach((x) => (x.onclick = () => { $("#tend").value = (x.dataset.v / 100).toFixed(2); upd(); }));
      focusLater("#tend");
    } else if (mode === "upi") {
      body.innerHTML = `<div class="qr" id="qrBox"><div class="mute small">Loading QR…</div></div>
        <div class="small mute" style="text-align:center">${st.upi ? `Customer scans and pays <b>${rs(total)}</b> to ${E(st.upi_vpa)}. Check the payment arrived, then tap Done.` : "Set the shop's UPI ID in Settings to show a QR with the amount here."}</div>
        <label class="f"><span>UPI reference (optional)</span><input class="in" id="ref" placeholder="last 4–12 digits"></label>`;
      showQR(total);
    } else if (mode === "card") {
      body.innerHTML = `<div class="note">Take ${rs(total)} on the card machine, then tap Done.</div><label class="f"><span>Card / approval code (optional)</span><input class="in" id="ref"></label>`;
    } else if (mode === "split") {
      body.innerHTML = `<div class="grid2">${["cash", "upi", "card"].map((m) => `<label class="f"><span>${MODE[m]} (₹)</span><input class="in" data-sp="${m}" inputmode="decimal"></label>`).join("")}</div>
        <div class="change" id="spLeft"></div><div class="small mute" style="margin-top:6px">Tip: type the cash part, the rest goes to UPI.</div>`;
      const upd = (src) => {
        const v = {};
        $$("[data-sp]").forEach((i) => (v[i.dataset.sp] = GST.paise(i.value) || 0));
        if (src && src.dataset.sp === "cash" && !$('[data-sp="card"]').value) {
          $('[data-sp="upi"]').value = v.cash < total ? ((total - v.cash) / 100).toFixed(2) : "";
          v.upi = Math.max(0, total - v.cash);
        }
        const left = total - v.cash - v.upi - v.card;
        $("#spLeft").textContent = left === 0 ? "Adds up ✓" : left > 0 ? `${rs(left)} still to pay` : `${rs(-left)} too much`;
      };
      $$("[data-sp]").forEach((i) => (i.oninput = () => upd(i)));
      upd();
      focusLater('[data-sp="cash"]');
    } else if (mode === "credit") {
      if (!b.ledger) {
        body.innerHTML = `<div class="note">Credit (udhaar) goes on a customer's account in Tally. Pick the customer first.</div><button class="btn block" id="pickC">Pick customer</button>`;
        $("#pickC").onclick = () => { closeSheet(); customerSheet(); };
      } else if (!me().can_credit) {
        body.innerHTML = `<div class="note bad">You aren't allowed to give credit. Ask the owner.</div>`;
      } else {
        body.innerHTML = `<div class="note ok">On <b>${E(b.ledger)}</b>'s account in Tally.</div>
          <div class="grid2"><label class="f"><span>Paid now in cash (₹)</span><input class="in" data-cr="cash" inputmode="decimal"></label>
          <label class="f"><span>Paid now by UPI (₹)</span><input class="in" data-cr="upi" inputmode="decimal"></label></div><div class="change" id="crLeft"></div>`;
        const upd = () => { const p = $$("[data-cr]").reduce((a, i) => a + (GST.paise(i.value) || 0), 0); $("#crLeft").textContent = p > total ? "More than the bill" : `On credit: ${rs(total - p)}`; };
        $$("[data-cr]").forEach((i) => (i.oninput = upd));
        upd();
      }
    }
  };
  async function showQR(amount) {
    if (!st.upi) return ($("#qrBox").innerHTML = "");
    try {
      const r = await api(`/api/qr?amount=${amount}&note=${encodeURIComponent("Bill " + nextNumberPreview())}`);
      $("#qrBox") && ($("#qrBox").innerHTML = r.svg);
    } catch (e) {
      $("#qrBox") && ($("#qrBox").innerHTML = `<div class="note">Offline: show the shop's printed UPI QR. UPI ID: <b>${E(st.upi_vpa)}</b></div>`);
    }
  }
  const done = () => {
    let pays = [], tendered = null;
    if (mode === "cash") {
      const t = $("#tend").value ? GST.paise($("#tend").value) : total;
      if (isNaN(t) || t < total) return toast("Cash received is less than the bill. Use Split for part cash.", true);
      pays = [{ mode: "cash", amount: total }];
      tendered = t;
    } else if (mode === "upi" || mode === "card") {
      pays = [{ mode, amount: total, ref: ($("#ref").value || "").trim() }];
    } else if (mode === "split") {
      $$("[data-sp]").forEach((i) => { const v = GST.paise(i.value) || 0; if (v) pays.push({ mode: i.dataset.sp, amount: v }); });
      const sum = pays.reduce((a, p) => a + p.amount, 0);
      if (sum !== total) return toast(`The parts add up to ${rs(sum)}, not ${rs(total)}.`, true);
    } else {
      if (!b.ledger || !me().can_credit) return toast("Pick a Tally customer for credit.", true);
      $$("[data-cr]").forEach((i) => { const v = GST.paise(i.value) || 0; if (v) pays.push({ mode: i.dataset.cr, amount: v }); });
      if (pays.reduce((a, p) => a + p.amount, 0) > total) return toast("Paid now is more than the bill.", true);
    }
    finishBill(c, pays, tendered);
  };
  $$(".modes button", s).forEach((x) => (x.onclick = () => { mode = x.dataset.m; draw(); }));
  $("#pBack").onclick = closeSheet;
  $("#pOk").onclick = done;
  S.payDone = done;
  draw();
}
function finishBill(c, pays, tendered) {
  const cn = counter();
  if (!cn) return chooseCounter();
  const day = todayISO();
  const fy = fyOf(day);
  const nums = LS.get("dk_nums", {});
  const key = `${cn.id}:${fy}`;
  const n = Math.max(nums[key] || 0, ((S.boot.numbers || {})[cn.id] || {})[fy] || 0) + 1;
  nums[key] = n;
  LS.set("dk_nums", nums);
  const number = `${settings().prefix || ""}${cn.series}/${fy}/${n}`;
  const bill = {
    uid: newId(), counter_id: cn.id, number, date: day, time: nowTime(), created: Date.now() / 1000,
    lines: S.cart.lines.map((l) => ({ item: l.item, qty: l.qty, price: l.price, mrp: l.mrp || 0, gst: l.gst, disc: l.disc || 0, hsn: l.hsn })),
    bill_disc: S.cart.bill_disc || 0, round: settings().round !== false, buyer: S.cart.buyer || {},
    payments: pays, tendered, amount: c.amount, link: linkId(), offline: !S.online,
  };
  S.queue.push(bill);
  LS.set("dk_queue", S.queue);
  const rec = { ...bill, kind: "sale", calc: c, units: Object.fromEntries(S.cart.lines.map((l) => [l.item, l.unit])), counter: cn.name, cashier: me().name };
  S.recent.unshift(rec);
  S.recent = S.recent.slice(0, 150);
  LS.set("dk_recent", S.recent);
  newCart();
  closeSheet();
  receiptSheet(rec, true);
  updDot();
  sync();
}

// ---------------------------------------------------------------- receipts
function receiptSheet(rec, fresh) {
  const st = settings();
  const phone = (rec.buyer || {}).phone;
  const s = sheet(fresh ? "Bill saved" : `${rec.kind === "return" ? "Return" : "Bill"} ${E(rec.number)}`, `
    ${fresh ? `<div class="paytot"><div class="a num" style="font-size:30px">${rs(rec.calc.amount)}</div><div class="mute">${E(rec.number)}${rec.tendered && rec.tendered > rec.calc.amount ? ` · <b style="color:var(--pri)">give back ${rs(rec.tendered - rec.calc.amount)}</b>` : ""}</div></div>` : ""}
    <div class="preview" style="width:${st.print.width === "58" ? "220px" : "320px"}">${receiptHTML(rec)}</div>
    <label class="f"><span>WhatsApp to (mobile)</span><div class="row"><input class="in grow" id="waPh" inputmode="tel" value="${E(phone || "")}" placeholder="10-digit mobile"><button class="btn" id="waGo">WhatsApp</button></div></label>`,
  `<button class="btn" id="rPrint">Print</button><button class="btn" id="rRaw" title="Bluetooth printer with the RawBT app (Android)">RawBT</button><button class="btn pri" id="rNew">${fresh ? "New bill" : "Close"}</button>`);
  $("#rPrint").onclick = () => printReceipt(rec);
  $("#rRaw").onclick = () => rawbt(rec);
  $("#waGo").onclick = () => whatsapp(rec, $("#waPh").value);
  $("#rNew").onclick = () => { closeSheet(); if (fresh && $("#q")) $("#q").focus(); };
  S.receiptOpen = rec;
  if (fresh && st.print.auto) setTimeout(() => printReceipt(rec), 200);
}
function shopInfo() { return settings().shop || {}; }
function receiptHTML(rec, a4) {
  const sh = shopInfo();
  const c = rec.calc;
  const ret = rec.kind === "return";
  const title = ret ? "CREDIT NOTE" : (c.title || "Tax Invoice").toUpperCase();
  const code = (sh.gstin || "").slice(0, 2);
  const b = rec.buyer || {};
  const units = rec.units || {};
  let h = `<div class="rc${a4 ? " a4" : ""}"><div class="c big">${E(sh.name || "")}</div>
    <div class="c">${E(sh.address || "").replace(/\n/g, "<br>")}</div>
    ${sh.phone ? `<div class="c">Ph: ${E(sh.phone)}</div>` : ""}
    <div class="c b">GSTIN: ${E(sh.gstin || "")}</div>${sh.fssai ? `<div class="c">FSSAI: ${E(sh.fssai)}</div>` : ""}
    <hr><div class="c b">${E(title)}</div><hr>
    <table><tr><td>${ret ? "CN" : "Bill"} No: <b>${E(rec.number)}</b></td><td class="r">${E(niceDate(rec.date))} ${E((rec.time || "").slice(0, 5))}</td></tr>
    <tr><td colspan="2">${E(rec.counter || "")} · ${E(rec.cashier || "")}</td></tr></table>`;
  if (ret) h += `<div>Against bill ${E(rec.orig_number)} dt ${E(niceDate(rec.orig_date))}</div>`;
  if (b.ledger || b.name || b.gstin) {
    h += `<div>To: <b>${E(b.name || b.ledger)}</b>${b.phone ? " " + E(b.phone) : ""}</div>${b.address ? `<div>${E(b.address)}</div>` : ""}${b.gstin ? `<div>GSTIN: <b>${E(b.gstin)}</b></div>` : ""}`;
  } else if (b.phone) h += `<div>Ph: ${E(b.phone)}</div>`;
  h += `<hr><table>`;
  c.lines.forEach((l, i) => {
    const src = (rec.lines || [])[i] || {};
    const u = units[l.item] || src.unit || "";
    h += `<tr><td colspan="2">${E(l.item)}</td></tr><tr><td class="small">&nbsp;${GST.qty(l.qm)}${u ? " " + E(u) : ""}${l.price ? " x " + GST.rs(l.price) : ""} <span style="opacity:.75">HSN ${E(src.hsn || "")} ${l.gst}%</span></td><td class="r">${GST.rs(l.amount)}</td></tr>`;
    if (l.disc) h += `<tr><td class="small">&nbsp;Discount</td><td class="r">-${GST.rs(l.disc)}</td></tr>`;
  });
  const gross = c.lines.reduce((a, l) => a + (l.gross || l.amount), 0);
  const disc = c.lines.reduce((a, l) => a + (l.disc || 0), 0);
  h += `</table><hr><table><tr><td>Items: ${c.lines.length}</td><td class="r">${GST.rs(gross)}</td></tr>`;
  if (disc) h += `<tr><td>Discount</td><td class="r">-${GST.rs(disc)}</td></tr>`;
  if (c.round_off) h += `<tr><td>Round off</td><td class="r">${c.round_off > 0 ? "+" : "-"}${GST.rs(Math.abs(c.round_off))}</td></tr>`;
  h += `<tr><td class="tt">${ret ? "REFUND" : "TOTAL"}</td><td class="r tt">Rs ${GST.rs(c.amount)}</td></tr></table>`;
  h += `<hr><table><tr><td class="small">GST%</td><td class="r small">Taxable</td><td class="r small">CGST</td><td class="r small">SGST</td></tr>`;
  for (const t of c.taxes) h += `<tr><td>${t.gst}%</td><td class="r">${GST.rs(t.taxable)}</td><td class="r">${GST.rs(t.cgst)}</td><td class="r">${GST.rs(t.sgst)}</td></tr>`;
  h += `<tr><td class="b">Total</td><td class="r b">${GST.rs(c.taxable)}</td><td class="r b">${GST.rs(c.cgst)}</td><td class="r b">${GST.rs(c.sgst)}</td></tr></table><hr>`;
  if (ret) h += `<div>Refund: ${E(MODE[rec.refund] || rec.refund)}</div>`;
  else {
    const pays = rec.payments || [];
    for (const p of pays) h += `<div>Paid ${E(MODE[p.mode])}: Rs ${GST.rs(p.amount)}${p.ref ? " (" + E(p.ref) + ")" : ""}</div>`;
    if (rec.tendered && rec.tendered > c.amount) h += `<div>Cash given Rs ${GST.rs(rec.tendered)}, change Rs ${GST.rs(rec.tendered - c.amount)}</div>`;
    const due = c.amount - pays.reduce((a, p) => a + p.amount, 0);
    if (due > 0) h += `<div class="b">On credit: Rs ${GST.rs(due)}</div>`;
    if (c.savings) h += `<div class="c b">You saved Rs ${GST.rs(c.savings)} on MRP</div>`;
  }
  h += `<div class="small">${E(GST.words(c.amount))}</div>
    <div class="small">Place of supply: ${E(sh.state || "")}${code ? " (" + code + ")" : ""} · Reverse charge: No</div>
    <hr><div class="c">${E((settings().print || {}).footer || "")}</div><div class="c small">Computer-generated ${ret ? "credit note" : "invoice"}. For ${E(sh.name || "")}</div></div>`;
  return h;
}
function printReceipt(rec) {
  const w = (settings().print || {}).width || "80";
  const css = w === "a4" ? "@page{size:A4;margin:12mm} #printarea{width:100%}" : `@page{size:${w}mm auto;margin:2mm} #printarea{width:${w === "58" ? 52 : 74}mm} .rc{font-size:${w === "58" ? 10.5 : 12}px}`;
  $("#pagestyle").textContent = "@media print{" + css + "}";
  $("#printarea").innerHTML = receiptHTML(rec, w === "a4");
  setTimeout(() => window.print(), 50);
}
function receiptText(rec, W) {
  const sh = shopInfo(), c = rec.calc, ret = rec.kind === "return";
  const L = [];
  const center = (s) => { s = String(s).slice(0, W); const p = Math.floor((W - s.length) / 2); return " ".repeat(Math.max(0, p)) + s; };
  const lr = (a, b) => { a = String(a); b = String(b); const sp = W - a.length - b.length; return sp >= 1 ? a + " ".repeat(sp) + b : a.slice(0, W - b.length - 1) + " " + b; };
  const wrap = (s) => { const out = []; s = String(s); while (s.length > W) { out.push(s.slice(0, W)); s = s.slice(W); } out.push(s); return out; };
  const hr = "-".repeat(W);
  L.push(["B", center(sh.name || "")]);
  for (const a of String(sh.address || "").split(/\n|, /)) if (a.trim()) L.push(center(a.trim()));
  if (sh.phone) L.push(center("Ph: " + sh.phone));
  L.push(center("GSTIN: " + (sh.gstin || "")));
  if (sh.fssai) L.push(center("FSSAI: " + sh.fssai));
  L.push(hr, ["B", center(ret ? "CREDIT NOTE" : (c.title || "Tax Invoice").toUpperCase())], hr);
  L.push(lr((ret ? "CN " : "Bill ") + rec.number, niceDate(rec.date) + " " + (rec.time || "").slice(0, 5)));
  L.push((rec.counter || "") + " / " + (rec.cashier || ""));
  if (ret) L.push("Against " + rec.orig_number + " dt " + niceDate(rec.orig_date));
  const b = rec.buyer || {};
  if (b.ledger || b.name) L.push(...wrap("To: " + (b.name || b.ledger) + (b.phone ? " " + b.phone : "")));
  if (b.gstin) L.push("GSTIN: " + b.gstin);
  L.push(hr);
  c.lines.forEach((l, i) => {
    const src = (rec.lines || [])[i] || {};
    const u = (rec.units || {})[l.item] || "";
    L.push(...wrap(l.item));
    L.push(lr(` ${GST.qty(l.qm)}${u ? " " + u : ""}${l.price ? " x " + GST.rs(l.price) : ""} ${l.gst}%`, GST.rs(l.amount)));
    if (l.disc) L.push(lr("  discount", "-" + GST.rs(l.disc)));
    void src;
  });
  L.push(hr);
  if (c.round_off) L.push(lr("Round off", GST.rs(c.round_off)));
  L.push(["B", lr(ret ? "REFUND" : "TOTAL", "Rs " + GST.rs(c.amount))], hr);
  L.push(lr("GST% Taxable", "CGST    SGST"));
  for (const t of c.taxes) L.push(lr(`${t.gst}% ${GST.rs(t.taxable)}`, `${GST.rs(t.cgst)} ${GST.rs(t.sgst)}`));
  L.push(hr);
  if (!ret) {
    for (const p of rec.payments || []) L.push(lr("Paid " + MODE[p.mode], GST.rs(p.amount)));
    if (rec.tendered && rec.tendered > c.amount) L.push(lr("Change", GST.rs(rec.tendered - c.amount)));
    const due = c.amount - (rec.payments || []).reduce((a, p) => a + p.amount, 0);
    if (due > 0) L.push(lr("On credit", GST.rs(due)));
    if (c.savings) L.push(center("You saved Rs " + GST.rs(c.savings)));
  } else L.push("Refund: " + (MODE[rec.refund] || rec.refund));
  L.push("Place of supply: " + (sh.state || "") + " " + (sh.gstin || "").slice(0, 2));
  L.push("Reverse charge: No");
  for (const f of wrap((settings().print || {}).footer || "")) L.push(center(f));
  L.push(center("Computer-generated " + (ret ? "credit note" : "invoice")));
  return L;
}
function rawbt(rec) {
  const W = (settings().print || {}).width === "58" ? 32 : 48;
  const lines = receiptText(rec, W);
  const bytes = [0x1b, 0x40];
  const enc = (s) => { for (const ch of String(s).replace(/₹/g, "Rs")) { const c = ch.charCodeAt(0); bytes.push(c < 128 ? c : 63); } };
  for (const l of lines) {
    if (Array.isArray(l)) { bytes.push(0x1b, 0x45, 1); enc(l[1]); bytes.push(0x1b, 0x45, 0); } else enc(l);
    bytes.push(0x0a);
  }
  bytes.push(0x0a, 0x0a, 0x0a, 0x1d, 0x56, 0x41, 0x10);
  const b64 = btoa(String.fromCharCode.apply(null, bytes));
  location.href = "intent:base64," + b64 + "#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;";
}
function whatsapp(rec, phone) {
  let ph = String(phone || "").replace(/\D/g, "");
  if (ph.length === 10) ph = "91" + ph;
  if (!/^91[6-9]\d{9}$/.test(ph)) return toast("Enter the customer's 10-digit mobile number.", true);
  const st = settings(), sh = shopInfo(), c = rec.calc;
  let msg = `*${sh.name || "Bill"}*\n${rec.kind === "return" ? "Credit note" : "Bill"} ${rec.number} · ${niceDate(rec.date)}\n`;
  if (st.public_url && rec.link) msg += `Amount: ₹${GST.rs(c.amount)}\nView your bill: ${st.public_url}/b/${rec.link}\n`;
  else {
    for (const l of c.lines) msg += `${l.item} ${GST.qty(l.qm)} — ₹${GST.rs(l.amount)}\n`;
    msg += `*Total ₹${GST.rs(c.amount)}* (GST ₹${GST.rs(c.cgst + c.sgst)})\n`;
  }
  if (c.savings) msg += `You saved ₹${GST.rs(c.savings)} 🎉\n`;
  msg += (st.print || {}).footer || "";
  window.open(`https://wa.me/${ph}?text=${encodeURIComponent(msg)}`, "_blank");
}

// ---------------------------------------------------------------- sending bills to the server
async function sync() {
  if (S.syncing || !S.queue.length || !S.token) return updDot();
  S.syncing = true;
  try {
    const batch = S.queue.slice(0, 40);
    const r = await api("/api/bills", { bills: batch.map((b) => { const x = { ...b }; delete x._error; return x; }) });
    const done = new Set();
    for (const res of r.results) {
      const b = S.queue.find((x) => x.uid === res.uid);
      if (!b) continue;
      if (res.status === "ok") {
        done.add(res.uid);
        const rec = S.recent.find((x) => x.uid === res.uid);
        if (rec) { rec.number = res.number; rec.link = res.link || rec.link; rec.flag = res.flag; rec.sent = true; }
        if (res.flag && !res.again) toast(`Bill ${res.number} is marked for the owner: ${res.flag}`, true);
      } else if (res.error) b._error = res.error;
    }
    S.queue = S.queue.filter((x) => !done.has(x.uid));
    LS.set("dk_queue", S.queue);
    LS.set("dk_recent", S.recent);
  } catch (e) {
    if (e.status && e.status !== 401) toast(e.message, true);
  } finally {
    S.syncing = false;
    updDot();
  }
}
function syncInfo() {
  const errs = S.queue.filter((b) => b._error);
  sheet("Connection", `<p>${S.online ? "Connected to the Dukaan server." : "Can't reach the Dukaan server right now. Billing carries on; bills are kept on this device."}</p>
    <p><b>${S.queue.length}</b> bill(s) waiting to be sent from this device.</p>
    ${errs.map((b) => `<div class="note bad"><b>${E(b.number)}</b>: ${E(b._error)}</div>`).join("")}
    <p class="small mute">Items and prices last loaded ${S.boot && S.boot.now ? new Date(S.boot.now * 1000).toLocaleString() : "-"}.</p>`,
  `<button class="btn" id="sReload">Reload items</button><button class="btn pri" id="sNow">Send now</button>`);
  $("#sNow").onclick = async () => { await sync(); closeSheet(); toast(S.queue.length ? `${S.queue.length} still waiting` : "All bills sent."); };
  $("#sReload").onclick = async () => { await loadBoot(); closeSheet(); if (S.view === "bill") { renderResults(); renderCart(); } toast(S.online ? "Items reloaded." : "Still offline.", !S.online); };
}

// ---------------------------------------------------------------- camera scanning
async function camera(onCode) {
  const single = typeof onCode === "function";
  if (!("BarcodeDetector" in window)) return toast("This browser can't scan with the camera. Use Chrome on Android, or a USB/Bluetooth barcode scanner.", true);
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }); } catch (e) { return toast("Camera not allowed. Allow it in the browser's site settings (needs https).", true); }
  const det = new window.BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "qr_code"] });
  const box = document.createElement("div");
  box.id = "cam";
  box.innerHTML = `<video playsinline muted></video><div class="frame"></div><div class="bar"><span class="grow" id="camMsg">Point at a barcode</span><button class="btn" id="camX">Done</button></div>`;
  document.body.appendChild(box);
  const v = $("video", box);
  v.srcObject = stream;
  await v.play().catch(() => {});
  let stop = false, last = "", lastAt = 0;
  const end = () => { stop = true; stream.getTracks().forEach((t) => t.stop()); box.remove(); };
  $("#camX", box).onclick = end;
  const tick = async () => {
    if (stop) return;
    try {
      const codes = await det.detect(v);
      if (codes.length) {
        const code = codes[0].rawValue;
        if (code !== last || Date.now() - lastAt > 1800) {
          last = code; lastAt = Date.now();
          if (single) { beep(true); end(); return onCode(code); }
          const it = S.byCode[code.toLowerCase()];
          if (it) { addItem(it); $("#camMsg").textContent = "Added " + it.name; } else { beep(false); $("#camMsg").textContent = "No item has barcode " + code; }
        }
      }
    } catch (e) { /* frame not ready */ }
    setTimeout(tick, 150);
  };
  tick();
}

// ---------------------------------------------------------------- keyboard and USB scanners
let wedge = { buf: "", t: 0 };
function keys(e) {
  if (S.pinKeys && !$("#lgPw")) return S.pinKeys(e);
  if (e.key === "Escape" && $(".sheet")) { if (!$(".sheet .x")) return; closeSheet(); return; }
  if ($(".sheet")) {
    if (e.key === "Enter" && S.payDone && $(".sheet .paytot") && !["TEXTAREA"].includes(document.activeElement.tagName) && document.activeElement.tagName !== "INPUT") { e.preventDefault(); S.payDone(); }
    return;
  }
  if (S.view !== "bill") return;
  if (e.key === "F2" || (e.key === "/" && document.activeElement.tagName !== "INPUT")) { e.preventDefault(); $("#q") && $("#q").focus(); return; }
  if (e.key === "F8" || e.key === "F12") { e.preventDefault(); paySheet(); return; }
  if (e.key === "F4") { e.preventDefault(); customerSheet(); return; }
  if (e.key === "F3") { e.preventDefault(); holdBill(); return; }
  // a scanner types fast and ends with Enter; catch it even when the search box isn't focused
  const tag = document.activeElement.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  const now = Date.now();
  if (now - wedge.t > 60) wedge.buf = "";
  wedge.t = now;
  if (e.key === "Enter" && wedge.buf.length >= 4) {
    const it = S.byCode[wedge.buf.toLowerCase()];
    if (it) addItem(it); else { beep(false); toast("No item has barcode " + wedge.buf, true); }
    wedge.buf = "";
  } else if (e.key.length === 1) wedge.buf += e.key;
}

// ---------------------------------------------------------------- bills list and one bill
function viewBills() {
  const today = todayISO();
  $("#app").innerHTML = `<div class="card"><div class="row wrap"><h2 class="grow" style="margin:0">Bills</h2>
    <input type="date" class="in" id="bDay" value="${S.billsDay || today}" style="width:auto">
    <div class="seg" id="bSt"><button data-s="" class="on">All</button>${isOwner() ? '<button data-s="tally">Not in Tally</button><button data-s="flag">Marked</button>' : ""}</div></div>
    <input class="in" id="bQ" placeholder="Search number, customer or mobile" style="margin-top:10px">
    <div id="bList" class="list" style="margin-top:6px"></div></div>`;
  let st = "";
  $$("#bSt button").forEach((b) => (b.onclick = () => { st = b.dataset.s; $$("#bSt button").forEach((x) => x.classList.toggle("on", x === b)); load(); }));
  $("#bDay").onchange = () => { S.billsDay = $("#bDay").value; load(); };
  let tm;
  $("#bQ").oninput = () => { clearTimeout(tm); tm = setTimeout(load, 250); };
  async function load() {
    const day = $("#bDay").value || today;
    const q = $("#bQ").value.trim();
    const waiting = S.queue.filter((b) => b.date === day && (!q || (b.number + " " + JSON.stringify(b.buyer)).toLowerCase().includes(q.toLowerCase())));
    let rows = [];
    let err = "";
    try { rows = (await api(`/api/bills?from=${day}&to=${day}&state=${st}&q=${encodeURIComponent(q)}`)).bills; } catch (e) { err = e.message; }
    const local = S.recent.filter((r) => r.date === day && !rows.find((x) => x.uid === r.uid) && !waiting.find((w) => w.uid === r.uid) && err);
    const html = [
      ...waiting.map((b) => `<div class="it" data-local="${b.uid}"><div class="grow"><b>${E(b.number)}</b> <span class="chip ${b._error ? "bad" : "warn"}">${b._error ? "Not accepted" : "Not sent yet"}</span><div class="small mute">${E(b.time)} · ${E((b.buyer || {}).name || (b.buyer || {}).ledger || "Walk-in")}</div></div><b class="num">${rs(b.amount)}</b></div>`),
      ...rows.map((b) => `<div class="it" data-uid="${b.uid}"><div class="grow"><b>${E(b.number)}</b> ${b.kind === "return" ? '<span class="chip acc">Return</span>' : ""}
        ${b.status === "cancelled" ? '<span class="chip bad">Cancelled</span>' + (b.tally === "cancel_failed" ? ' <span class="chip bad">Not cancelled in Tally</span>' : b.tally === "cancelling" ? ' <span class="chip warn">Cancelling in Tally</span>' : "") : b.flag ? '<span class="chip bad">Marked</span>' : b.tally === "posted" ? '<span class="chip ok">In Tally</span>' : b.tally === "failed" ? '<span class="chip bad">Tally refused</span>' : '<span class="chip">Not in Tally yet</span>'}
        <div class="small mute">${E(b.time.slice(0, 5))} · ${E(b.customer || "Walk-in")} · ${b.modes.map((m) => MODE[m] || m).join(" + ") || "Credit"} · ${E(b.cashier || "")}</div></div>
        <b class="num" style="${b.kind === "return" ? "color:var(--bad)" : ""}">${b.kind === "return" ? "−" : ""}${rs(b.amount)}</b></div>`),
      ...local.map((r) => `<div class="it" data-recent="${r.uid}"><div class="grow"><b>${E(r.number)}</b> <span class="chip">on this device</span></div><b class="num">${rs(r.calc.amount)}</b></div>`),
    ].join("");
    $("#bList").innerHTML = (err ? `<div class="note">${E(err)} Showing bills kept on this device.</div>` : "") + (html || '<div class="empty">No bills on this day.</div>');
    $$("#bList .it").forEach((el) => (el.onclick = () => {
      if (el.dataset.uid) return billDetail(el.dataset.uid, load);
      const rec = S.recent.find((r) => r.uid === (el.dataset.local || el.dataset.recent));
      if (rec) receiptSheet(rec);
    }));
  }
  load();
}
async function billDetail(uid, reload) {
  let b;
  try { b = await api("/api/bill/" + uid); } catch (e) { return toast(e.message, true); }
  const c = b.calc || { lines: [], taxes: [] };
  const d = b.data;
  const rec = toRec(b);
  const TC = { cancelling: '<span class="chip warn">Being cancelled in Tally</span>', cancelled: '<span class="chip">Cancelled in Tally too</span>',
    cancel_failed: '<span class="chip bad">Couldn\'t cancel in Tally</span>' };
  const chips = b.status === "cancelled" ? '<span class="chip bad">Cancelled</span> ' + (TC[b.tally] || "") : (b.flag ? '<span class="chip bad">Marked for owner</span> ' : "") +
    (b.tally === "posted" ? '<span class="chip ok">In Tally</span>' : b.tally === "failed" ? '<span class="chip bad">Tally refused it</span>' : '<span class="chip">Not in Tally yet</span>');
  const s = sheet(`${b.kind === "return" ? "Return" : "Bill"} ${E(b.number)}`, `
    <div class="row wrap">${chips}<span class="grow"></span><span class="small mute">${E(niceDate(b.date))} ${E(b.time)} · ${E(d.counter)} · ${E(b.cashier || "")}</span></div>
    ${b.status === "cancelled" ? `<div class="note">Cancelled by ${E(b.cancelled_by || "")}: ${E(b.cancel_reason || "")}</div>` : ""}${b.flag ? `<div class="note bad">${E(b.flag)}</div>` : ""}${b.tally_error ? `<div class="note bad">Tally: ${E(b.tally_error)}</div>` : ""}
    ${b.kind === "return" ? `<div class="note">Against bill <b>${E(d.orig_number)}</b> (${E(niceDate(d.orig_date))}). Refund: ${E(MODE[d.refund])}${d.reason ? ". " + E(d.reason) : ""}</div>` : ""}
    ${d.buyer.ledger || d.buyer.name ? `<p><b>${E(d.buyer.ledger || d.buyer.name)}</b> ${E(d.buyer.phone || "")} ${d.buyer.gstin ? "· GSTIN " + E(d.buyer.gstin) : ""}</p>` : ""}
    <div class="tw"><table class="t"><tr><th>Item</th><th class="r">Qty</th><th class="r">GST</th><th class="r">Taxable</th><th class="r">Amount</th></tr>
    ${c.lines.map((l, i) => `<tr><td>${E(l.item)}<div class="small mute">HSN ${E((d.lines[i] || {}).hsn || "")}${l.disc ? " · disc " + rs(l.disc) : ""}</div></td><td class="r">${GST.qty(l.qm)}</td><td class="r">${l.gst}%</td><td class="r num">${rs(l.taxable)}</td><td class="r num">${rs(l.amount)}</td></tr>`).join("")}
    <tr class="sum"><td colspan="3">CGST ${rs(c.cgst)} · SGST ${rs(c.sgst)}${c.round_off ? " · round off " + rs(c.round_off) : ""}</td><td class="r num">${rs(c.taxable)}</td><td class="r num">${rs(c.amount)}</td></tr></table></div>
    ${b.kind === "sale" ? `<p>${(d.payments || []).map((p) => `${MODE[p.mode]} ${rs(p.amount)}`).join(" + ") || ""}${c.amount > (d.payments || []).reduce((a, p) => a + p.amount, 0) ? ` <span class="chip warn">credit ${rs(c.amount - (d.payments || []).reduce((a, p) => a + p.amount, 0))}</span>` : ""}</p>` : ""}
    ${b.returns && b.returns.length ? `<p class="small">Returns: ${b.returns.map((r) => `${E(r.number)} (${rs(r.amount)})`).join(", ")}</p>` : ""}
    ${isOwner() && b.posts.length ? `<h3>In Tally</h3>${b.posts.map((p) => `<div class="small"><span class="chip ${p.status === "done" ? "ok" : p.status === "failed" ? "bad" : ""}">${p.status === "done" ? "Added" : p.status === "failed" ? "Refused" : "Waiting"}</span> ${E(p.label)}${p.error ? ' — <span style="color:var(--bad)">' + E(p.error) + "</span>" : ""}</div>`).join("")}` : ""}`,
  `<button class="btn" id="dPrint">Print / WhatsApp</button>
   ${b.kind === "sale" && b.status === "ok" && me().can_return && (b.returnable || []).some((x) => x > 0) ? '<button class="btn" id="dRet">Return items</button>' : ""}
   ${isOwner() && b.flag && b.status === "ok" ? '<button class="btn acc" id="dOk">Looks right, send</button>' : ""}
   ${isOwner() && (b.tally === "failed" || b.tally === "cancel_failed") ? '<button class="btn" id="dRetry">Try Tally again</button>' : ""}
   ${isOwner() && b.status === "ok" && !(b.returns || []).some((r) => r.status === "ok") ? '<button class="btn bad" id="dCancel">Cancel bill</button>' : ""}`, { wide: true });
  $("#dPrint").onclick = () => receiptSheet(rec);
  const act = async (path, body, msg) => { try { await api(path, body); toast(msg); closeSheet(); reload && reload(); } catch (e) { toast(e.message, true); } };
  $("#dOk") && ($("#dOk").onclick = () => act(`/api/bill/${uid}/approve`, {}, "It will go to Tally."));
  $("#dRetry") && ($("#dRetry").onclick = () => act(`/api/bill/${uid}/retry`, {}, "Trying again."));
  $("#dCancel") && ($("#dCancel").onclick = () => {
    const inTally = (b.posts || []).some((p) => p.status === "done" || p.status === "sending" || p.status === "unknown");
    sheet(`Cancel ${E(b.number)}?`, `${inTally
      ? `<div class="note">This bill is already in Tally. Dukaan will <b>cancel</b> it there too, using Tally's own Cancel: the voucher and its number stay in Tally, marked cancelled, with the amounts made zero. Nothing is deleted.${(b.posts || []).length > 1 ? " The contra or receipt made with it is cancelled too." : ""}</div>
         <p class="small mute">This is only allowed until GSTR-1 for the bill's month is due (the 10th of next month). After that, take the goods back as a return.</p>`
      : `<div class="note">This bill hasn't gone to Tally, so it is cancelled only in Dukaan.</div>`}
      <p class="small mute">The bill number stays used and is reported as cancelled in GSTR-1 (documents issued).</p>
      <label class="f"><span>Why is it being cancelled?</span><input class="in" id="cWhy" placeholder="e.g. wrong items billed"></label>`,
    `<button class="btn" id="cNo">Keep the bill</button><button class="btn bad" id="cYes">Cancel the bill</button>`);
    $("#cNo").onclick = closeSheet;
    $("#cYes").onclick = () => {
      const why = $("#cWhy").value.trim();
      if (!why) return toast("Say why the bill is being cancelled.", true);
      act(`/api/bill/${uid}/cancel`, { reason: why }, inTally ? "Cancelled. Cancelling it in Tally now." : "Bill cancelled.");
    };
    focusLater("#cWhy");
  });
  $("#dRet") && ($("#dRet").onclick = () => returnSheet(b, reload));
  void s;
}
function toRec(b) {
  const d = b.data;
  return { uid: b.uid, kind: b.kind, number: b.number, date: b.date, time: b.time, calc: b.calc, lines: d.lines, buyer: d.buyer,
    payments: d.payments, tendered: d.tendered, counter: d.counter, cashier: b.cashier, link: b.link, refund: d.refund,
    orig_number: d.orig_number, orig_date: d.orig_date, units: Object.fromEntries((d.lines || []).map((l) => [l.item, l.unit])) };
}
function returnSheet(b, reload) {
  const c = b.calc;
  sheet(`Return against ${E(b.number)}`, `<p class="small mute">Enter how much of each item comes back. The refund is worked out from what the customer paid, with the same GST.</p>
    <div class="tw"><table class="t"><tr><th>Item</th><th class="r">Billed</th><th class="r">Can return</th><th class="r">Returning</th></tr>
    ${c.lines.map((l, i) => `<tr><td>${E(l.item)}</td><td class="r">${GST.qty(l.qm)}</td><td class="r">${GST.qty(b.returnable[i])}</td>
      <td class="r"><input class="in" style="width:90px;text-align:right" data-i="${i}" inputmode="decimal" ${b.returnable[i] > 0 ? "" : "disabled"}></td></tr>`).join("")}</table></div>
    <label class="f"><span>Money goes back as</span><select class="in" id="rMode"><option value="cash">Cash</option><option value="upi">UPI</option><option value="card">Card</option>${b.data.buyer.ledger ? `<option value="credit">Less from ${E(b.data.buyer.ledger)}'s account</option>` : ""}</select></label>
    <label class="f"><span>Reason</span><input class="in" id="rWhy" placeholder="e.g. damaged, wrong item"></label>
    <div id="rPrev" style="margin-top:10px"></div>`,
  `<button class="btn" id="rCalc">Work out refund</button><button class="btn pri" id="rGo" disabled>Make credit note</button>`, { wide: true });
  const picks = () => $$("[data-i]").filter((i) => i.value.trim()).map((i) => ({ index: Number(i.dataset.i), qty: i.value.trim() }));
  let ok = false;
  $$("[data-i]").forEach((i) => (i.oninput = () => { ok = false; $("#rGo").disabled = true; $("#rPrev").innerHTML = ""; }));
  $("#rCalc").onclick = async () => {
    try {
      const r = await api("/api/return/preview", { orig_uid: b.uid, lines: picks() });
      $("#rPrev").innerHTML = `<div class="note ok">Refund <b>${rs(r.calc.amount)}</b> (taxable ${rs(r.calc.taxable)} + CGST ${rs(r.calc.cgst)} + SGST ${rs(r.calc.sgst)}${r.calc.round_off ? ", round off " + rs(r.calc.round_off) : ""})</div>`;
      ok = true; $("#rGo").disabled = false;
    } catch (e) { toast(e.message, true); }
  };
  $("#rGo").onclick = async () => {
    if (!ok) return;
    try {
      const r = await api("/api/return", { uid: newId(), orig_uid: b.uid, counter_id: S.counterId, lines: picks(), refund: $("#rMode").value, reason: $("#rWhy").value });
      const full = await api("/api/bill/" + r.uid);
      closeSheet();
      toast(`Credit note ${r.number} made.`);
      receiptSheet(toRec(full));
      reload && reload();
    } catch (e) { toast(e.message, true); }
  };
}

// ---------------------------------------------------------------- reports (owner)
function viewReports() {
  const t = todayISO();
  const d = new Date();
  const y = todayISO(new Date(d.getFullYear(), d.getMonth(), d.getDate() - 1));
  const m1 = todayISO(new Date(d.getFullYear(), d.getMonth(), 1));
  const pm1 = todayISO(new Date(d.getFullYear(), d.getMonth() - 1, 1)), pm2 = todayISO(new Date(d.getFullYear(), d.getMonth(), 0));
  const ranges = { today: [t, t], yesterday: [y, y], month: [m1, t], last: [pm1, pm2] };
  const [f0, t0] = S.repRange || ranges.today;
  $("#app").innerHTML = `<div class="row wrap" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Reports</h2>
    <div class="seg" id="rr"><button data-r="today">Today</button><button data-r="yesterday">Yesterday</button><button data-r="month">This month</button><button data-r="last">Last month</button></div>
    <input type="date" class="in" id="rf" value="${f0}" style="width:auto"><input type="date" class="in" id="rt" value="${t0}" style="width:auto"></div><div id="rep"></div>`;
  $$("#rr button").forEach((b) => (b.onclick = () => { const [a, z] = ranges[b.dataset.r]; $("#rf").value = a; $("#rt").value = z; load(); }));
  $("#rf").onchange = $("#rt").onchange = load;
  async function load() {
    const f = $("#rf").value, to = $("#rt").value;
    S.repRange = [f, to];
    $$("#rr button").forEach((b) => b.classList.toggle("on", ranges[b.dataset.r][0] === f && ranges[b.dataset.r][1] === to));
    $("#rep").innerHTML = '<div class="card mute">Loading…</div>';
    let s, h, dc;
    try {
      [s, h, dc] = await Promise.all([api(`/api/reports/summary?from=${f}&to=${to}`), api(`/api/reports/hsn?from=${f}&to=${to}`), api(`/api/reports/docs?from=${f}&to=${to}`)]);
    } catch (e) { $("#rep").innerHTML = `<div class="note bad">${E(e.message)}</div>`; return; }
    const kpi = (l, v, sub) => `<div class="kpi"><div class="l">${l}</div><div class="v num">${v}</div>${sub ? `<div class="small mute">${sub}</div>` : ""}</div>`;
    const sum = (arr, k) => arr.reduce((a, r) => a + r[k], 0);
    $("#rep").innerHTML = `
      ${s.not_in_tally || s.flagged ? `<div class="note">${s.not_in_tally} bill(s) not in Tally yet${s.flagged ? `, ${s.flagged} marked for you to check` : ""}. <a href="#tally">See Tally page</a></div>` : ""}
      <div class="kpis">${kpi("Net sales", rs(s.net), `${s.bills} bills, avg ${rs(s.average)}`)}${kpi("Cash in drawer", rs(s.cash_in_hand), "cash sales less cash refunds")}
        ${kpi("UPI", rs(s.modes.upi - s.refunds.upi))}${kpi("Card", rs(s.modes.card - s.refunds.card))}${kpi("On credit", rs(s.modes.credit - s.refunds.credit))}
        ${kpi("Returns", rs(s.returns), `${s.return_count} credit notes`)}${kpi("Discounts given", rs(s.discount))}${s.cancelled ? kpi("Cancelled bills", s.cancelled) : ""}</div>
      <div class="cols" style="margin-top:12px">
        <div class="card"><h3 style="margin-top:0">GST by rate</h3><div class="tw"><table class="t"><tr><th>Rate</th><th class="r">Taxable</th><th class="r">CGST</th><th class="r">SGST</th><th class="r">Total</th></tr>
          ${s.rates.map((r) => `<tr><td>${r.gst == null ? "Round off" : r.gst + "%"}</td><td class="r num">${r.gst == null ? "" : rs(r.taxable)}</td><td class="r num">${r.gst == null ? "" : rs(r.cgst)}</td><td class="r num">${r.gst == null ? "" : rs(r.sgst)}</td><td class="r num">${rs(r.amount)}</td></tr>`).join("")}
          <tr class="sum"><td>Total</td><td class="r num">${rs(sum(s.rates, "taxable"))}</td><td class="r num">${rs(sum(s.rates, "cgst"))}</td><td class="r num">${rs(sum(s.rates, "sgst"))}</td><td class="r num">${rs(sum(s.rates, "amount"))}</td></tr></table></div>
          <p class="small mute">Sales less returns. Your GST return is still filed from Tally.</p></div>
        <div class="card"><h3 style="margin-top:0">By cashier and counter</h3><table class="t">${s.cashiers.map(([n, a]) => `<tr><td>${E(n)}</td><td class="r num">${rs(a)}</td></tr>`).join("")}</table>
          <table class="t" style="margin-top:10px">${s.counters.map(([n, a]) => `<tr><td>${E(n)}</td><td class="r num">${rs(a)}</td></tr>`).join("")}</table>
          ${s.days.length > 1 ? `<h3>By day</h3><table class="t">${s.days.map((x) => `<tr><td>${E(niceDate(x.date))}</td><td class="r">${x.bills} bills</td><td class="r num">${rs(x.amount)}</td></tr>`).join("")}</table>` : ""}</div>
      </div>
      <div class="card" style="margin-top:12px"><div class="row"><h3 class="grow" style="margin:0">HSN summary (GSTR-1 table 12)</h3><button class="btn sm" id="dlH">Download CSV</button></div>
        <div class="tw"><table class="t"><tr><th></th><th>HSN</th><th>Unit</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Taxable</th><th class="r">CGST</th><th class="r">SGST</th><th class="r">Value</th></tr>
        ${h.rows.map((r) => `<tr><td><span class="chip ${r.b2b ? "acc" : ""}">${r.b2b ? "B2B" : "B2C"}</span></td><td>${E(r.hsn) || '<span style="color:var(--bad)">missing</span>'}</td><td>${E(r.unit)}</td><td class="r">${GST.qty(r.qm)}</td><td class="r">${r.gst}%</td><td class="r num">${rs(r.taxable)}</td><td class="r num">${rs(r.cgst)}</td><td class="r num">${rs(r.sgst)}</td><td class="r num">${rs(r.value)}</td></tr>`).join("") || '<tr><td colspan="9" class="mute">No sales.</td></tr>'}</table></div></div>
      <div class="card" style="margin-top:12px"><h3 style="margin-top:0">Documents issued (GSTR-1 table 13)</h3><div class="tw"><table class="t"><tr><th>Document</th><th>From</th><th>To</th><th class="r">Total</th><th class="r">Cancelled</th><th class="r">Net issued</th></tr>
        ${dc.rows.map((r) => `<tr><td>${E(r.kind)}</td><td>${E(r.from)}</td><td>${E(r.to)}</td><td class="r">${r.total}</td><td class="r">${r.cancelled}</td><td class="r">${r.net}</td></tr>${r.missing.length ? `<tr><td colspan="6" class="small" style="color:var(--warn)">Numbers not received from a counter (counted as cancelled): ${r.missing.join(", ")}</td></tr>` : ""}`).join("") || '<tr><td colspan="6" class="mute">None.</td></tr>'}</table></div></div>
      <div class="card" style="margin-top:12px"><div class="row"><h3 class="grow" style="margin:0">Top items</h3><button class="btn sm" id="dlB">Download all bills (CSV)</button></div>
        <table class="t">${s.top.map((x) => `<tr><td>${E(x.item)}</td><td class="r">${GST.qty(x.qm)}</td><td class="r num">${rs(x.amount)}</td></tr>`).join("")}</table></div>`;
    $("#dlH").onclick = () => download(`/api/reports/hsn.csv?from=${f}&to=${to}`, `hsn-${f}-${to}.csv`);
    $("#dlB").onclick = () => download(`/api/reports/bills.csv?from=${f}&to=${to}`, `bills-${f}-${to}.csv`);
  }
  load();
}
async function download(path, name) {
  try {
    const r = await api(path, undefined, true);
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- items (owner)
async function viewItems() {
  $("#app").innerHTML = `<div class="card"><div class="row wrap"><h2 class="grow" style="margin:0">Items</h2>
    <div class="seg" id="iF"><button data-f="all" class="on">All</button><button data-f="bad">Can't sell</button><button data-f="fav">Favourites</button><button data-f="hidden">Hidden</button></div></div>
    <input class="in" id="iQ" placeholder="Search items" style="margin-top:10px"><div id="iInfo" class="small mute" style="margin:8px 0"></div><div id="iList" class="list"></div></div>`;
  let items = [], f = "all";
  try { items = (await api("/api/items")).items; } catch (e) { $("#iList").innerHTML = `<div class="note bad">${E(e.message)}</div>`; return; }
  const draw = () => {
    const q = $("#iQ").value.toLowerCase();
    const r = items.filter((i) => (f === "all" ? !i.hidden : f === "bad" ? !i.can_sell || i.issues.length : f === "fav" ? i.fav : i.hidden) && (!q || (i.name + " " + i.codes.join(" ")).toLowerCase().includes(q)));
    const bad = items.filter((i) => !i.can_sell).length;
    $("#iInfo").innerHTML = `${items.length} items from Tally. ${bad ? `<b style="color:var(--bad)">${bad} can't be sold yet</b> (missing GST rate or HSN, or price above MRP).` : "All items can be sold."} Prices, barcodes and favourites are kept in Dukaan; Tally isn't changed.`;
    $("#iList").innerHTML = r.slice(0, 300).map((i) => `<div class="it" data-n="${E(i.name)}"><div class="grow"><b>${E(i.name)}</b> ${i.fav ? "⭐" : ""}
      <div class="small mute">${rs(i.price)}${i.mrp ? " · MRP " + rs(i.mrp) : ""} · GST ${i.gst == null ? "?" : i.gst + "%"} · HSN ${E(i.hsn || "?")} · ${i.codes.length ? i.codes.length + " barcode" + (i.codes.length > 1 ? "s" : "") : "no barcode"} · stock ${i.stock == null ? "-" : +i.stock.toFixed(3)} ${E(i.unit)}</div>
      ${i.issues.length ? `<div class="small" style="color:var(--bad)">${E(i.issues.join(" · "))}</div>` : ""}</div></div>`).join("") + (r.length > 300 ? `<div class="empty">${r.length - 300} more. Search to narrow down.</div>` : "") || '<div class="empty">No items.</div>';
    $$("#iList .it").forEach((el) => (el.onclick = () => itemSheet(items.find((x) => x.name === el.dataset.n), (ni) => { Object.assign(items.find((x) => x.name === ni.name), ni); draw(); })));
  };
  $$("#iF button").forEach((b) => (b.onclick = () => { f = b.dataset.f; $$("#iF button").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  $("#iQ").oninput = draw;
  draw();
}
function itemSheet(i, done) {
  let codes = [...(i.codes || [])];
  const s = sheet(E(i.name), `
    <div class="small mute">${E(i.group || "")} · unit ${E(i.unit)} · in Tally: price ${rs(i.tally.price)}${i.tally.mrp ? ", MRP " + rs(i.tally.mrp) : ""}, GST ${i.tally.gst == null ? "not set" : i.tally.gst + "%"}, HSN ${E(i.tally.hsn || "not set")}</div>
    <div class="grid2"><label class="f"><span>Selling price (₹, GST included)</span><input class="in" id="ip" inputmode="decimal" value="${(i.price / 100).toFixed(2)}"></label>
    <label class="f"><span>MRP (₹)</span><input class="in" id="im" inputmode="decimal" value="${i.mrp ? (i.mrp / 100).toFixed(2) : ""}"></label></div>
    <label class="f"><span>Barcodes</span><div class="row"><input class="in grow" id="ic" placeholder="Scan or type, then Add"><button class="btn" id="icAdd">Add</button><button class="btn" id="icCam">${ICON.scan}</button></div></label>
    <div id="icList" class="row wrap" style="margin-top:6px"></div>
    <label class="chk"><input type="checkbox" id="il" ${i.loose ? "checked" : ""}> Loose / weighed (ask for the quantity each time)</label>
    <label class="chk"><input type="checkbox" id="ifv" ${i.fav ? "checked" : ""}> Favourite (quick button on the billing screen)</label>
    <label class="chk"><input type="checkbox" id="ih" ${i.hidden ? "checked" : ""}> Hide from billing</label>
    <details ${i.tally.gst == null || !i.tally.hsn ? "open" : ""}><summary><b>GST rate and HSN</b> ${i.gst_from === "dukaan" ? '<span class="chip warn">set in Dukaan</span>' : ""}</summary>
      <div class="note">These should come from Tally. Set them here only if Tally doesn't give them to Dukaan, and set the same in Tally's item master, or Tally's GST reports won't match.</div>
      <div class="grid2"><label class="f"><span>GST rate (%)</span><select class="in" id="ig"><option value="">From Tally (${i.tally.gst == null ? "not set" : i.tally.gst + "%"})</option>${GST.SLABS.map((g) => `<option ${i.gst_from === "dukaan" && i.gst === g ? "selected" : ""}>${g}</option>`).join("")}</select></label>
      <label class="f"><span>HSN code</span><input class="in" id="ihs" inputmode="numeric" placeholder="${E(i.tally.hsn || "")}" value="${i.hsn !== i.tally.hsn ? E(i.hsn) : ""}"></label></div></details>`,
  `<button class="btn" id="iX">Cancel</button><button class="btn pri" id="iOk">Save</button>`);
  const drawCodes = () => {
    $("#icList").innerHTML = codes.map((c) => `<span class="chip">${E(c)} <a href="#" data-c="${E(c)}" style="color:var(--bad);text-decoration:none">✕</a></span>`).join("") || '<span class="small mute">No barcodes.</span>';
    $$("#icList a").forEach((a) => (a.onclick = (e) => { e.preventDefault(); codes = codes.filter((x) => x !== a.dataset.c); drawCodes(); }));
  };
  const add = (v) => { v = String(v || "").trim(); if (v && !codes.includes(v)) codes.push(v); $("#ic").value = ""; drawCodes(); };
  $("#icAdd").onclick = () => add($("#ic").value);
  $("#ic").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); add($("#ic").value); } };
  $("#icCam").onclick = () => camera(add);
  drawCodes();
  $("#iX").onclick = closeSheet;
  $("#iOk").onclick = async () => {
    const price = GST.paise($("#ip").value), mrp = $("#im").value.trim() ? GST.paise($("#im").value) : 0;
    if (isNaN(price) || isNaN(mrp)) return toast("Check the price and MRP.", true);
    try {
      const r = await api("/api/item", { name: i.name, price: price === i.tally.price ? null : price, mrp: mrp === i.tally.mrp ? null : mrp, codes,
        loose: $("#il").checked, fav: $("#ifv").checked, hidden: $("#ih").checked, gst: $("#ig").value, hsn: $("#ihs").value.trim() });
      closeSheet();
      toast("Saved. Counters get it when they next reload items.");
      done(r.item);
      loadBoot(true);
    } catch (e) { toast(e.message, true); }
  };
  void s;
}

// ---------------------------------------------------------------- Tally (owner)
async function viewTally() {
  $("#app").innerHTML = '<div class="card mute">Loading…</div>';
  let t;
  try { t = await api("/api/tally"); } catch (e) { $("#app").innerHTML = `<div class="note bad">${E(e.message)}</div>`; return; }
  const s = t.setup || {}, ch = t.choices, st = t.state;
  const opt = (list, v) => `<option value="">— pick —</option>` + list.map((n) => `<option ${n === v ? "selected" : ""}>${E(n)}</option>`).join("") + (v && !list.includes(v) ? `<option selected>${E(v)}</option>` : "");
  const sel = (id, label, list, v, help) => `<label class="f"><span>${label}</span><select class="in" id="${id}">${opt(list, v)}</select>${help ? `<span class="small mute">${help}</span>` : ""}</label>`;
  const ref = t.refreshed || {};
  $("#app").innerHTML = `
    <div class="cols">
      <div class="card"><h2>Connection to Tally</h2>
        <label class="f"><span>Tally's address (the Tally PC's IP, port 9000)</span><div class="row"><input class="in grow" id="tUrl" value="${E(t.url)}" placeholder="192.168.1.20:9000 or Tailscale IP"><button class="btn" id="tCon">Connect</button></div></label>
        <div class="small mute" style="margin-top:6px">In TallyPrime: F1 Help › Settings › Connectivity › Client/Server configuration: <b>TallyPrime acts as: Both</b>, <b>Enable ODBC: Yes</b>, port <b>9000</b>. Keep your company open in Tally.</div>
        <div style="margin-top:12px">${st.tally === "ok" ? '<span class="chip ok">Sending bills</span>' : `<span class="chip warn">${E(st.tally)}</span>`} ${st.busy ? `<span class="chip">${E(st.busy)}</span>` : ""}</div>
        ${st.error ? `<div class="note bad">${E(st.error)}</div>` : ""}${st.refresh_error ? `<div class="note bad">Copying from Tally: ${E(st.refresh_error)}</div>` : ""}
        <p class="small">Company: <b>${E(s.company || "-")}</b>. ${ref.at ? `Items and ledgers copied ${new Date(ref.at * 1000).toLocaleString()} (${ref.items} items, ${ref.ledgers} ledgers).` : "Not copied yet."}</p>
        <div class="row wrap"><button class="btn" id="tRef">Copy items &amp; ledgers now</button>
          <label class="row small">every <input class="in" id="tMin" style="width:70px" value="${t.refresh_minutes}" inputmode="numeric"> min</label></div>
        <h3>Bills</h3>
        <div class="row wrap"><span class="chip ok">${t.counts.posted || 0} in Tally</span><span class="chip">${(t.counts.pending || 0) + (t.counts.sending || 0)} waiting</span><span class="chip ${t.counts.failed ? "bad" : ""}">${t.counts.failed || 0} refused</span><span class="chip ${t.flagged.length ? "bad" : ""}">${t.flagged.length} marked</span></div>
        ${t.failed.length ? `<h3>Refused by Tally</h3>${t.failed.map((b) => `<div class="note bad" data-uid="${b.uid}" style="cursor:pointer"><b>${E(b.number)}</b>: ${E(b.tally_error)}</div>`).join("")}<button class="btn" id="tRetry">Fix in Tally, then try all again</button>` : ""}
        ${t.flagged.length ? `<h3>Marked for you to check</h3>${t.flagged.map((b) => `<div class="note" data-uid="${b.uid}" style="cursor:pointer"><b>${E(b.number)}</b>: ${E(b.flag)}</div>`).join("")}` : ""}
      </div>
      <div class="card"><h2>Where bills go in Tally</h2>
        ${s.confirmed ? '<div class="note ok">Confirmed. New bills go to Tally automatically.</div>' : '<div class="note">Dukaan has guessed these from your ledgers. Check each one, then confirm. Nothing goes to Tally until you do.</div>'}
        ${t.problems.length ? `<div class="note bad">${t.problems.map(E).join("<br>")}</div>` : ""}
        <div class="grid2">
          ${sel("s_sales_vtype", "Voucher type for bills", ch.vtypes.Sales, s.sales_vtype, "Set its numbering to Manual in Tally.")}
          ${sel("s_return_vtype", "Voucher type for returns", ch.vtypes["Credit Note"], s.return_vtype)}
          ${sel("s_receipt_vtype", "Receipts (credit bills paid now)", ch.vtypes.Receipt, s.receipt_vtype)}
          ${sel("s_contra_vtype", "Contra (split payments)", ch.vtypes.Contra, s.contra_vtype)}
          ${sel("s_cash", "Cash ledger", ch.cash, s.cash)}
          ${sel("s_upi", "UPI payments go to", ch.bank, s.upi)}
          ${sel("s_card", "Card payments go to", ch.bank, s.card)}
          ${sel("s_round_off", "Round off ledger", ch.other, s.round_off)}
          ${sel("s_godown", "Stock goes out of godown", ch.godowns, s.godown)}
        </div>
        <h3>By GST rate</h3>
        <div class="tw"><table class="t"><tr><th>Rate</th><th>Sales ledger</th><th>CGST ledger</th><th>SGST ledger</th></tr>
        ${ch.rates.map((r) => { const k = String(r); return `<tr><td><b>${k}%</b></td><td><select class="in" data-sl="${k}">${opt(ch.sales, (s.sales_ledgers || {})[k])}</select></td>
          <td>${r ? `<select class="in" data-cg="${k}">${opt(ch.cgst, (s.cgst || {})[k])}</select>` : "-"}</td><td>${r ? `<select class="in" data-sg="${k}">${opt(ch.sgst, (s.sgst || {})[k])}</select>` : "-"}</td></tr>`; }).join("") || '<tr><td colspan="4" class="mute">Copy items from Tally first.</td></tr>'}</table></div>
        <label class="chk"><input type="checkbox" id="sConf" ${s.confirmed ? "checked" : ""}> I've checked these. Send bills to Tally.</label>
        <button class="btn pri block" id="sSave">Save</button>
        <p class="small mute">Dukaan adds new vouchers to Tally and never deletes anything. The only change it makes is cancelling (with Tally’s Cancel) the vouchers of a bill you cancel.</p>
      </div>
    </div>`;
  $("#tCon").onclick = async () => {
    try { const r = await api("/api/tally/connect", { url: $("#tUrl").value, refresh_minutes: Number($("#tMin").value) || 30 }); toast(`Connected. Company: ${r.company}`); await api("/api/tally/refresh", {}); viewTally(); } catch (e) { toast(e.message, true); }
  };
  $("#tRef").onclick = async () => { toast("Copying from Tally…"); try { const r = await api("/api/tally/refresh", {}); toast(`Copied ${r.result.items} items and ${r.result.ledgers} ledgers.`); loadBoot(true); viewTally(); } catch (e) { toast(e.message, true); } };
  $("#tRetry") && ($("#tRetry").onclick = async () => { await api("/api/tally/retry", {}); toast("Trying again."); setTimeout(viewTally, 2500); });
  $$("[data-uid]").forEach((el) => (el.onclick = () => billDetail(el.dataset.uid, viewTally)));
  $("#sSave").onclick = async () => {
    const body = { confirmed: $("#sConf").checked, sales_ledgers: {}, cgst: {}, sgst: {} };
    for (const k of ["sales_vtype", "return_vtype", "receipt_vtype", "contra_vtype", "cash", "upi", "card", "round_off", "godown"]) body[k] = $("#s_" + k).value;
    $$("[data-sl]").forEach((x) => (body.sales_ledgers[x.dataset.sl] = x.value));
    $$("[data-cg]").forEach((x) => (body.cgst[x.dataset.cg] = x.value));
    $$("[data-sg]").forEach((x) => (body.sgst[x.dataset.sg] = x.value));
    try { await api("/api/tally/setup", body); toast(body.confirmed ? "Saved. Bills will go to Tally." : "Saved."); viewTally(); } catch (e) { toast(e.message, true); }
  };
}

// ---------------------------------------------------------------- settings (owner)
async function viewSettings() {
  let d;
  try { d = await api("/api/settings"); } catch (e) { $("#app").innerHTML = `<div class="note bad">${E(e.message)}</div>`; return; }
  const s = d.settings, sh = s.shop;
  $("#app").innerHTML = `<div class="cols">
    <div class="card"><h2>Shop details on the bill</h2>
      <label class="f"><span>Shop name</span><input class="in" id="sName" value="${E(sh.name)}"></label>
      <label class="f"><span>Address</span><textarea class="in" id="sAddr" rows="2">${E(sh.address)}</textarea></label>
      <div class="grid2"><label class="f"><span>GSTIN</span><input class="in" id="sG" value="${E(sh.gstin)}" maxlength="15" style="text-transform:uppercase"></label>
      <label class="f"><span>State</span><input class="in" id="sSt" value="${E(sh.state)}" placeholder="from the GSTIN"></label>
      <label class="f"><span>Phone</span><input class="in" id="sPh" value="${E(sh.phone)}"></label>
      <label class="f"><span>FSSAI licence no.</span><input class="in" id="sF" value="${E(sh.fssai)}"></label></div>
      <p class="small mute">Left empty, the details come from your company in Tally.</p>
      <h3>Printing</h3>
      <div class="grid2"><label class="f"><span>Paper</span><select class="in" id="pW"><option value="80" ${s.print.width === "80" ? "selected" : ""}>80 mm roll</option><option value="58" ${s.print.width === "58" ? "selected" : ""}>58 mm roll</option><option value="a4" ${s.print.width === "a4" ? "selected" : ""}>A4 / A5 sheet</option></select></label>
      <label class="f"><span>Bottom line</span><input class="in" id="pF" value="${E(s.print.footer)}"></label></div>
      <label class="chk"><input type="checkbox" id="pA" ${s.print.auto ? "checked" : ""}> Print every bill as soon as it's saved</label>
      <h3>UPI</h3>
      <div class="grid2"><label class="f"><span>Shop's UPI ID</span><input class="in" id="uV" value="${E(s.upi.vpa)}" placeholder="shop@okhdfcbank"></label>
      <label class="f"><span>Name shown to the customer</span><input class="in" id="uN" value="${E(s.upi.name)}"></label></div>
      <h3>Bills</h3>
      <div class="grid2"><label class="f"><span>Bill number prefix (optional)</span><input class="in" id="bP" value="${E(s.prefix)}" maxlength="5" placeholder="e.g. GK"></label>
      <label class="f"><span>HSN digits needed</span><select class="in" id="bH"><option value="6" ${s.hsn_digits !== 4 ? "selected" : ""}>6 (turnover above ₹5 crore)</option><option value="4" ${s.hsn_digits === 4 ? "selected" : ""}>4 (₹5 crore or less)</option></select></label></div>
      <label class="chk"><input type="checkbox" id="bR" ${s.round ? "checked" : ""}> Round bills to the rupee</label>
      <label class="chk"><input type="checkbox" id="bCR" ${s.cashier_reports ? "checked" : ""}> Cashiers can see reports</label>
      <label class="f"><span>Public address of this server (for bill links on WhatsApp)</span><input class="in" id="bU" value="${E(s.public_url)}" placeholder="https://dukaan.yourdomain.com"></label>
      <button class="btn pri block" style="margin-top:14px" id="sSave">Save</button>
    </div>
    <div>
      <div class="card"><h2>Counters</h2><p class="small mute">Each counter (billing device) has its own bill number series, e.g. A/2627/1, B/2627/1.</p>
        <div class="list">${d.counters.map((c) => `<div class="it" data-c="${c.id}"><div class="grow"><b>${E(c.name)}</b> ${c.active ? "" : '<span class="chip">off</span>'}<div class="small mute">Series ${E(s.prefix)}${E(c.series)}/… ${c.device ? "· in use" : ""}</div></div></div>`).join("")}</div>
        <button class="btn" id="cAdd">Add counter</button></div>
      <div class="card" style="margin-top:12px"><h2>Cashiers</h2>
        <div class="list">${d.cashiers.map((c) => `<div class="it" data-u="${c.id}"><div class="grow"><b>${E(c.name)}</b> ${c.active ? "" : '<span class="chip">off</span>'}<div class="small mute">${[c.can_discount && "discounts", c.can_return && "returns", c.can_credit && "credit"].filter(Boolean).join(", ") || "billing only"}</div></div></div>`).join("") || '<div class="empty">No cashiers yet.</div>'}</div>
        <button class="btn" id="uAdd">Add cashier</button></div>
      <div class="card" style="margin-top:12px"><h2>Owner password</h2>
        <div class="grid2"><label class="f"><span>Current</span><input class="in" type="password" id="pwO"></label><label class="f"><span>New</span><input class="in" type="password" id="pwN"></label></div>
        <button class="btn" style="margin-top:10px" id="pwGo">Change password</button></div>
    </div></div>`;
  $("#sSave").onclick = async () => {
    try {
      await api("/api/settings", {
        shop: { name: $("#sName").value, address: $("#sAddr").value, gstin: $("#sG").value, state: $("#sSt").value, phone: $("#sPh").value, fssai: $("#sF").value, email: sh.email },
        print: { width: $("#pW").value, footer: $("#pF").value, auto: $("#pA").checked }, upi: { vpa: $("#uV").value, name: $("#uN").value },
        prefix: $("#bP").value, hsn_digits: Number($("#bH").value), round: $("#bR").checked, public_url: $("#bU").value, cashier_reports: $("#bCR").checked,
      });
      toast("Saved.");
      await loadBoot(true);
      afterBoot();
    } catch (e) { toast(e.message, true); }
  };
  const counterSheet = (c) => {
    c = c || { name: "", series: "", active: 1 };
    sheet(c.id ? "Counter" : "New counter", `<label class="f"><span>Name</span><input class="in" id="cn" value="${E(c.name)}" placeholder="Counter 2"></label>
      <label class="f"><span>Series (1–3 letters or digits)</span><input class="in" id="cs" value="${E(c.series)}" maxlength="3" style="text-transform:uppercase" placeholder="B"></label>
      ${c.id ? `<label class="chk"><input type="checkbox" id="ca" ${c.active ? "checked" : ""}> In use</label>` : ""}`,
    `<button class="btn pri" id="cOk">Save</button>`);
    $("#cOk").onclick = async () => { try { await api("/api/counter", { id: c.id, name: $("#cn").value, series: $("#cs").value, active: c.id ? $("#ca").checked : true }); closeSheet(); await loadBoot(true); viewSettings(); } catch (e) { toast(e.message, true); } };
  };
  $("#cAdd").onclick = () => counterSheet();
  $$("[data-c]").forEach((el) => (el.onclick = () => counterSheet(d.counters.find((c) => c.id === Number(el.dataset.c)))));
  const userSheet = (u) => {
    u = u || { name: "", active: 1, can_discount: 1, can_return: 0, can_credit: 0 };
    sheet(u.id ? E(u.name) : "New cashier", `<label class="f"><span>Name</span><input class="in" id="un" value="${E(u.name)}"></label>
      <label class="f"><span>${u.id ? "New PIN (leave empty to keep)" : "PIN (4–8 digits)"}</span><input class="in" id="up" inputmode="numeric" maxlength="8"></label>
      <label class="chk"><input type="checkbox" id="ud" ${u.can_discount ? "checked" : ""}> Can change prices and give discounts</label>
      <label class="chk"><input type="checkbox" id="ur" ${u.can_return ? "checked" : ""}> Can take returns</label>
      <label class="chk"><input type="checkbox" id="uc" ${u.can_credit ? "checked" : ""}> Can give credit (udhaar)</label>
      <label class="chk"><input type="checkbox" id="ua" ${u.active ? "checked" : ""}> Can sign in</label>`, `<button class="btn pri" id="uOk">Save</button>`);
    $("#uOk").onclick = async () => {
      try { await api("/api/cashier", { id: u.id, name: $("#un").value, pin: $("#up").value, can_discount: $("#ud").checked, can_return: $("#ur").checked, can_credit: $("#uc").checked, active: $("#ua").checked }); closeSheet(); viewSettings(); } catch (e) { toast(e.message, true); }
    };
  };
  $("#uAdd").onclick = () => userSheet();
  $$("[data-u]").forEach((el) => (el.onclick = () => userSheet(d.cashiers.find((c) => c.id === Number(el.dataset.u)))));
  $("#pwGo").onclick = async () => { try { await api("/api/password", { old: $("#pwO").value, new: $("#pwN").value }); toast("Password changed."); $("#pwO").value = $("#pwN").value = ""; } catch (e) { toast(e.message, true); } };
}

// ---------------------------------------------------------------- labels (owner)
const LABEL_SIZES = {
  "50x25": { name: "50 × 25 mm roll", w: 50, h: 25 },
  "38x25": { name: "38 × 25 mm roll", w: 38, h: 25 },
  "50x30": { name: "50 × 30 mm roll", w: 50, h: 30 },
  "50x38": { name: "50 × 38 mm roll", w: 50, h: 38 },
  "75x50": { name: "75 × 50 mm roll", w: 75, h: 50 },
  "100x50": { name: "100 × 50 mm roll", w: 100, h: 50 },
  "a4-65": { name: "A4 sheet, 65 labels (38.1 × 21.2 mm)", w: 38.1, h: 21.2, cols: 5, rows: 13, top: 10.7, left: 4.75, gx: 2.5, gy: 0 },
  "a4-40": { name: "A4 sheet, 40 labels (52.5 × 29.7 mm)", w: 52.5, h: 29.7, cols: 4, rows: 10, top: 0, left: 0, gx: 0, gy: 0 },
  "a4-24": { name: "A4 sheet, 24 labels (70 × 37 mm)", w: 70, h: 37, cols: 3, rows: 8, top: 0.5, left: 0, gx: 0, gy: 0 },
};
function lblState() {
  const d = { size: "50x25", opts: { taxes: true, packed: false, fssai: false }, list: [], skip: 0 };
  const st = LS.get("dk_labels", d);
  return { ...d, ...st, opts: { ...d.opts, ...(st.opts || {}) } };
}
function ddmmyy(iso) { if (!iso) return ""; const [y, m, d] = iso.split("-"); return `${d}/${m}/${y}`; }
function addMonths(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(y, m - 1 + n, d);
  if (t.getDate() !== d) t.setDate(0);          // 31 Jan + 1 month -> 28/29 Feb
  return todayISO(t);
}
function labelHTML(L, size, opts) {
  const sz = LABEL_SIZES[size];
  const f = Math.min(sz.w / 17, sz.h / 8.2);       // base font size in mm
  const sh = shopInfo();
  const save = L.mrp && L.price < L.mrp ? L.mrp - L.price : 0;
  const pct = save ? Math.round((save * 100) / L.mrp) : 0;
  let bc = "";
  if (L.code) { try { bc = Barcode.svg(L.code, { height: 40, text: false }); } catch (e) { bc = `<div style="color:red">${E(e.message)}</div>`; } }
  const foot = [opts.packed && sh.name ? `Packed by ${sh.name}` : "", opts.fssai && sh.fssai ? `FSSAI ${sh.fssai}` : ""].filter(Boolean).join(" · ");
  return `<div class="lbl" style="width:${sz.w}mm;height:${sz.h}mm;font-size:${f.toFixed(2)}mm">
    <div class="l-nm">${E(L.name)}${L.net ? ` <span class="l-net">${E(L.net)}</span>` : ""}</div>
    <div class="l-dt">${L.mfd ? `MFD: <b>${ddmmyy(L.mfd)}</b>` : ""}${L.exp ? ` &nbsp;EXP: <b>${ddmmyy(L.exp)}</b>` : ""}</div>
    <div class="l-pr">${save ? `<span class="l-mrp">MRP ₹${GST.rs(L.mrp)}</span><span class="l-our">₹${GST.rs(L.price)}</span><span class="l-save">Save ₹${GST.rs(save)}${pct ? ` (${pct}%)` : ""}</span>`
      : `<span class="l-our">${L.mrp ? "MRP " : ""}₹${GST.rs(L.price)}</span>`}</div>
    ${opts.taxes && L.mrp ? '<div class="l-tx">MRP incl. of all taxes</div>' : ""}
    ${bc ? `<div class="l-bc">${bc}</div><div class="l-code">${E(L.code)}</div>` : '<div class="l-bc"></div>'}
    ${foot ? `<div class="l-ft">${E(foot)}</div>` : ""}</div>`;
}
function viewLabels() {
  const st = lblState();
  const save = () => LS.set("dk_labels", st);
  let cur = { name: "", net: "", mfd: todayISO(), exp: "", mrp: 0, price: 0, code: "", copies: 1, item: "" };
  $("#app").innerHTML = `<div class="cols">
    <div class="card"><h2>Make a label</h2>
      <div class="search" style="position:relative"><span class="ic">${ICON.search}</span><input class="in" id="lQ" placeholder="Pick an item (or just type the label below)" autocomplete="off" style="padding-left:44px"></div>
      <div id="lRes" class="card hide" style="padding:0;position:absolute;z-index:5;max-height:300px;overflow:auto;width:min(560px,90vw)"></div>
      <label class="f"><span>Product name on the label</span><input class="in" id="lName"></label>
      <div class="grid2"><label class="f"><span>Net quantity (optional)</span><input class="in" id="lNet" placeholder="e.g. 1 kg, 500 g"></label>
        <label class="f"><span>Copies</span><input class="in" id="lCopies" inputmode="numeric" value="1"></label>
        <label class="f"><span>MFD (packed on)</span><input class="in" type="date" id="lMfd"></label>
        <label class="f"><span>EXP (use by)</span><input class="in" type="date" id="lExp"></label></div>
      <div class="quick" id="lQuick">${[["1 month", 1], ["3 months", 3], ["6 months", 6], ["9 months", 9], ["1 year", 12]].map(([t, n]) => `<button class="btn sm" data-m="${n}">+${t}</button>`).join("")}</div>
      <div class="grid2"><label class="f"><span>MRP (₹)</span><input class="in" id="lMrp" inputmode="decimal"></label>
        <label class="f"><span>Selling price (₹)</span><input class="in" id="lPrice" inputmode="decimal"></label></div>
      <div id="lSave" class="small" style="margin-top:6px"></div>
      <label class="f"><span>Barcode</span><div class="row"><input class="in grow" id="lCode" placeholder="from the item, or scan/type one"><button class="btn" id="lCam" title="Scan">${ICON.scan}</button><button class="btn" id="lNew">Make one</button></div></label>
      <div class="small mute" id="lCodeInfo" style="margin-top:4px"></div>
      <button class="btn pri block" style="margin-top:14px" id="lAdd">Add to print list</button>
    </div>
    <div>
      <div class="card"><div class="row"><h2 class="grow" style="margin:0">Preview</h2><span class="small mute" id="lSzTxt"></span></div>
        <div style="display:flex;justify-content:center;padding:14px;background:#e9ece8;border-radius:10px;margin-top:10px;overflow:auto"><div id="lPrev" style="zoom:2"></div></div></div>
      <div class="card" style="margin-top:12px"><h2>Print list</h2>
        <label class="f"><span>Label size</span><select class="in" id="lSize">${Object.entries(LABEL_SIZES).map(([k, v]) => `<option value="${k}" ${k === st.size ? "selected" : ""}>${v.name}</option>`).join("")}</select></label>
        <label class="f" id="lSkipRow"><span>Labels already used on the first sheet</span><input class="in" id="lSkip" inputmode="numeric" value="${st.skip || 0}"></label>
        <label class="chk"><input type="checkbox" id="oTax" ${st.opts.taxes ? "checked" : ""}> "MRP incl. of all taxes"</label>
        <label class="chk"><input type="checkbox" id="oPack" ${st.opts.packed ? "checked" : ""}> "Packed by" with the shop's name</label>
        <label class="chk"><input type="checkbox" id="oFssai" ${st.opts.fssai ? "checked" : ""}> Shop's FSSAI number</label>
        <div id="lList" class="list"></div>
        <div class="row" style="margin-top:10px"><button class="btn bad" id="lClear">Clear list</button><span class="grow"></span><button class="btn pri big" id="lPrint">Print labels</button></div>
        <p class="small mute">On a label printer, set the paper size in the printer's settings to the label size, and margins to None in the print window. Check that the first label scans before printing many.</p>
      </div>
    </div></div>`;
  $("#lMfd").value = cur.mfd;
  const read = () => {
    cur.name = $("#lName").value.trim(); cur.net = $("#lNet").value.trim();
    cur.mfd = $("#lMfd").value; cur.exp = $("#lExp").value;
    cur.mrp = $("#lMrp").value.trim() ? GST.paise($("#lMrp").value) : 0;
    cur.price = GST.paise($("#lPrice").value);
    cur.code = $("#lCode").value.trim(); cur.copies = Math.max(1, Math.min(500, parseInt($("#lCopies").value, 10) || 1));
  };
  const draw = () => {
    read();
    const sz = LABEL_SIZES[st.size];
    $("#lSzTxt").textContent = `${sz.w} × ${sz.h} mm, shown twice the size`;
    $("#lSkipRow").classList.toggle("hide", !sz.cols);
    const save = cur.mrp && cur.price < cur.mrp ? cur.mrp - cur.price : 0;
    $("#lSave").innerHTML = isNaN(cur.price) || isNaN(cur.mrp) ? '<span style="color:var(--bad)">Check the prices.</span>'
      : cur.mrp && cur.price > cur.mrp ? '<span style="color:var(--bad)">Selling price can\'t be above the MRP.</span>'
      : save ? `<span style="color:var(--ok)">Customer saves ${rs(save)} (${Math.round((save * 100) / cur.mrp)}%)</span>` : "";
    let info = "";
    if (cur.code) { try { Barcode.svg(cur.code); info = Barcode.isEan13(cur.code) ? "EAN-13 barcode" : "Code 128 barcode"; } catch (e) { info = `<span style="color:var(--bad)">${E(e.message)}</span>`; } }
    const it = S.byName[cur.item];
    if (cur.code && it && !it.codes.includes(cur.code)) info += " · not on the item yet: billing won't find it by this barcode";
    $("#lCodeInfo").innerHTML = info;
    $("#lPrev").innerHTML = labelHTML({ ...cur, name: cur.name || "Product name", price: isNaN(cur.price) ? 0 : cur.price }, st.size, st.opts);
    $("#lNew").disabled = !cur.item || !!(it && it.codes.length);
  };
  const pick = (it) => {
    cur.item = it.name;
    $("#lNet").value = ""; $("#lCopies").value = "1";
    $("#lName").value = it.name;
    $("#lMrp").value = it.mrp ? (it.mrp / 100).toFixed(2) : "";
    $("#lPrice").value = it.price ? (it.price / 100).toFixed(2) : "";
    $("#lCode").value = (it.codes || []).find((c) => Barcode.isEan13(c)) || (it.codes || [])[0] || "";
    $("#lQ").value = ""; $("#lRes").classList.add("hide");
    draw();
  };
  $("#lQ").oninput = () => {
    const r = searchItems($("#lQ").value).slice(0, 20);
    const box = $("#lRes");
    box.classList.toggle("hide", !r.length);
    box.innerHTML = r.map((i, k) => `<div class="res" data-k="${k}"><div class="grow"><div class="nm">${E(i.name)}</div><div class="small mute">${i.mrp ? "MRP " + rs(i.mrp) + " · " : ""}${i.codes.length ? i.codes[0] : "no barcode"}</div></div><b>${rs(i.price)}</b></div>`).join("");
    $$(".res", box).forEach((el) => (el.onclick = () => pick(r[el.dataset.k])));
  };
  $$("#app input").forEach((i) => i.addEventListener("input", draw));
  $$("#lQuick button").forEach((b) => (b.onclick = () => { $("#lExp").value = addMonths($("#lMfd").value || todayISO(), Number(b.dataset.m)); draw(); }));
  $("#lCam").onclick = () => camera((c) => { $("#lCode").value = c; draw(); });
  $("#lNew").onclick = async () => {
    try {
      const r = await api("/api/item/newcode", { name: cur.item });
      $("#lCode").value = r.code;
      const it = S.byName[cur.item];
      if (it) { it.codes = r.item.codes; S.byCode[r.code.toLowerCase()] = it; }
      toast(`Barcode ${r.code} given to ${cur.item}. Counters scan it after they reload items.`);
      draw();
    } catch (e) { toast(e.message, true); }
  };
  const drawList = () => {
    $("#lList").innerHTML = st.list.map((L, k) => `<div class="it"><div class="grow"><b>${E(L.name)}</b> ${L.net ? E(L.net) : ""}<div class="small mute">${L.mrp ? "MRP " + rs(L.mrp) + " · " : ""}${rs(L.price)} · ${L.mfd ? "MFD " + ddmmyy(L.mfd) : ""} ${L.exp ? "EXP " + ddmmyy(L.exp) : ""} · ${E(L.code || "no barcode")}</div></div>
      <input class="in" style="width:70px;text-align:center" data-c="${k}" value="${L.copies}" inputmode="numeric"><button class="btn sm ghost" data-x="${k}">✕</button></div>`).join("")
      || '<div class="empty">Nothing yet. Make a label and add it.</div>';
    $$("[data-x]").forEach((b) => (b.onclick = () => { st.list.splice(Number(b.dataset.x), 1); save(); drawList(); }));
    $$("[data-c]").forEach((i) => (i.onchange = () => { st.list[Number(i.dataset.c)].copies = Math.max(1, Math.min(500, parseInt(i.value, 10) || 1)); save(); drawList(); }));
    const n = st.list.reduce((a, L) => a + L.copies, 0);
    $("#lPrint").textContent = n ? `Print ${n} label${n > 1 ? "s" : ""}` : "Print labels";
    $("#lPrint").disabled = !n;
  };
  $("#lAdd").onclick = () => {
    read();
    if (!cur.name) return toast("Enter the product name.", true);
    if (!(cur.price > 0)) return toast("Enter the selling price.", true);
    if (isNaN(cur.mrp)) return toast("The MRP isn't a number.", true);
    if (cur.mrp && cur.price > cur.mrp) return toast("Selling price can't be above the MRP.", true);
    if (cur.exp && cur.mfd && cur.exp < cur.mfd) return toast("EXP is before MFD.", true);
    if (cur.code) { try { Barcode.svg(cur.code); } catch (e) { return toast(e.message, true); } }
    st.list.push({ ...cur });
    save(); drawList();
    toast(`Added ${cur.copies} × ${cur.name}`);
  };
  $("#lClear").onclick = () => { if (confirm("Clear the print list?")) { st.list = []; save(); drawList(); } };
  $("#lSize").onchange = () => { st.size = $("#lSize").value; save(); draw(); };
  $("#lSkip").onchange = () => { st.skip = Math.max(0, parseInt($("#lSkip").value, 10) || 0); save(); };
  const opt = () => { st.opts = { taxes: $("#oTax").checked, packed: $("#oPack").checked, fssai: $("#oFssai").checked }; save(); draw(); };
  $("#oTax").onchange = $("#oPack").onchange = $("#oFssai").onchange = opt;
  $("#lPrint").onclick = () => printLabels(st);
  draw(); drawList();
}
function printLabels(st) {
  const sz = LABEL_SIZES[st.size];
  const all = [];
  for (const L of st.list) for (let i = 0; i < L.copies; i++) all.push(labelHTML(L, st.size, st.opts));
  let html = "", css;
  if (!sz.cols) {
    css = `@page{size:${sz.w}mm ${sz.h}mm;margin:0} #printarea .lbl{break-after:page;page-break-after:always}`;
    html = all.join("");
  } else {
    const per = sz.cols * sz.rows;
    const cells = Array(Math.min(st.skip || 0, per - 1)).fill('<div class="lbl" style="visibility:hidden"></div>').concat(all);
    css = `@page{size:A4;margin:0} #printarea .lsheet{width:210mm;height:297mm;box-sizing:border-box;padding:${sz.top}mm 0 0 ${sz.left}mm;display:grid;
      grid-template-columns:repeat(${sz.cols},${sz.w}mm);grid-auto-rows:${sz.h}mm;column-gap:${sz.gx}mm;row-gap:${sz.gy}mm;break-after:page;page-break-after:always;overflow:hidden}`;
    for (let i = 0; i < cells.length; i += per) html += `<div class="lsheet">${cells.slice(i, i + per).join("")}</div>`;
  }
  $("#pagestyle").textContent = "@media print{" + css + "}";
  $("#printarea").innerHTML = html;
  setTimeout(() => window.print(), 80);
}

start();
