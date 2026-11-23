/* Robot Kontrol Merkezi: düz JS, derleme yok. Veri her zaman textContent ile yazılır (innerHTML yok).
 * Bu dosya İSKELETİN çekirdeği: oturum, canlı akış (WS), üst çubuk (ACİL DUR, bağlantı, mod, erişim) ve
 * uyarı bandı. Sayfalar web/pages/*.js içindedir (pages.js kaydı); yeni sayfa için bu dosyaya dokunulmaz. */
'use strict';

const $ = (id) => document.getElementById(id);
const S = { session: null, ui: null, snap: null, ws: null, wsOk: false, wsState: 'connecting', events: [],
            retry: 0, pollTimer: null, started: false, liveAt: 0 };

function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k === 'style') for (const [sk, sv] of Object.entries(v)) e.style.setProperty(sk, sv);
    else if (k.startsWith('data-')) e.setAttribute(k, v);
    else e[k] = v;
  }
  for (const c of children) e.append(c);
  return e;
}

class ApiError extends Error {
  constructor(status, body) { super(body && body.message || `HTTP ${status}`); this.status = status; this.body = body || {}; }
}

async function api(path, method = 'GET', body) {
  const opt = { method, credentials: 'same-origin', headers: {} };
  if (method !== 'GET') { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body || {}); }
  let res;
  try { res = await fetch(path, opt); } catch (e) { throw new ApiError(0, { message: 'Hub’a ulaşılamıyor' }); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* gövdesiz */ }
  if (!res.ok) {
    if (res.status === 401 && data && data.error === 'login_required') showLogin();
    throw new ApiError(res.status, data);
  }
  return data;
}

function toast(msg, bad = false) {
  const t = el('div', { class: 'toast' + (bad ? ' bad' : ''), text: msg });
  $('toasts').append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3500);
}

function applyTheme(theme) {
  for (const [k, v] of Object.entries(theme || {})) document.documentElement.style.setProperty(k, v);
}

/* ---------- açılış ---------- */
async function init() {
  $('btn-logout').addEventListener('click', logout);
  $('login-form').addEventListener('submit', onLogin);
  $('btn-estop').addEventListener('click', onEstop);
  $('btn-reset').addEventListener('click', onReset);
  $('btn-menu').addEventListener('click', () => $('nav').classList.toggle('open'));
  try {
    S.session = await api('/api/session');
  } catch (e) { setConn('down'); toast(e.message, true); setTimeout(init, 3000); return; }
  applyTheme(S.session.theme);
  $('title').textContent = S.session.title; document.title = S.session.title;
  if (S.session.mode === 'pin' && !S.session.authenticated) showLogin(); else start();
}

function showLogin() {
  $('main').hidden = true; $('nav').hidden = true; $('login').hidden = false; $('btn-logout').hidden = true;
  closeWs(); $('pin').value = ''; $('pin').focus(); setConn('down', 'Giriş gerekli'); renderTop(null);
}

async function onLogin(ev) {
  ev.preventDefault();
  $('login-error').textContent = '';
  try {
    await api('/api/login', 'POST', { pin: $('pin').value.trim() });
    S.session = await api('/api/session');
    if (S.started) { location.reload(); return; }       // oturum süresi dolduktan sonra: sayfalar temiz kurulsun
    start();
  } catch (e) {
    if (e.status === 429) $('login-error').textContent = `Çok fazla deneme. ${Math.ceil(e.body.retry_after_s || 60)} sn bekle.`;
    else if (e.status === 401) $('login-error').textContent = `PIN hatalı (${e.body.attempts_left} deneme hakkın kaldı).`;
    else $('login-error').textContent = e.message;
    $('pin').select();
  }
}

async function logout() { try { await api('/api/logout', 'POST'); } catch (e) { /* yok say */ } closeWs(); location.reload(); }

async function start() {
  $('login').hidden = true;
  try {
    S.ui = await api('/api/ui');
    S.snap = await api('/api/state');
  } catch (e) { toast(e.message, true); return; }
  applyTheme(S.ui.theme);
  document.documentElement.style.setProperty('--btn-h', S.ui.layout.button_height);
  S.started = true;
  S.events = [];
  $('nav').hidden = false; $('main').hidden = false;
  api('/api/events').then((r) => { S.events = r.events; HubPages.onEvents(S.events); }).catch(() => {});
  await HubPages.build();
  render();
  connectWs();
}

/* ---------- eylemler ---------- */
async function onEstop() {
  // Tek dokunuş, onay yok. Ağ hatasında da tekrar dene: durdurma sessizce başarısız olmamalı.
  for (let i = 0; i < 3; i++) {
    try {
      const r = await api('/api/estop', 'POST');
      if (r && r.interrupts_motion === false) toast('Dikkat: bu robot API v1 konuşmuyor; hareket ortasında kesilemeyebilir, sonraki adımlar iptal edildi.', true);
      else toast('ACİL DUR gönderildi');
      return;
    } catch (e) {
      if (i === 2) toast('ACİL DUR gönderilemedi: ' + e.message + ' — robotun fiziksel durdurmasını kullan!', true);
    }
  }
}
async function onReset() {
  try { await api('/api/estop/reset', 'POST'); toast('Kilit açıldı'); } catch (e) { toast(e.message, true); }
}

/* ---------- çizim ---------- */
function setConn(state, label) { S.wsState = state; S.connLabel = label || null; renderConn(); }
const fmt = (v) => (v == null ? '—' : v.toFixed(3));
const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString('tr-TR', { hour12: false });

function renderTop(s) {
  const m = ShellLogic.modeInfo(s), pm = $('pill-mode');
  pm.dataset.mode = m.key; pm.textContent = m.label; pm.title = m.title;
  const mode = s ? s.access.mode : (S.session && S.session.mode);
  const a = ShellLogic.accessInfo(mode, !!(s && s.me.authenticated) || !!(S.session && S.session.authenticated));
  const pa = $('pill-access'); pa.dataset.access = a.key; pa.textContent = a.label; pa.title = a.title;
  $('btn-logout').hidden = mode !== 'pin' || !(s && s.me.authenticated);
}

function renderConn() {
  const c = ShellLogic.connInfo(S.wsState, S.snap);
  const p = $('pill-conn'); p.dataset.state = c.state; p.textContent = S.connLabel || c.label;
}

function render() {
  const s = S.snap; if (!s || !S.ui) return;
  renderTop(s); renderConn();
  const readOnly = s.access.mode === 'locked';
  const banner = $('banner');
  const rb = s.robot || {}, est = s.estop, rep = est.report;
  const show = (cls, text) => { banner.hidden = false; banner.className = 'banner ' + cls; banner.textContent = text; };
  if (est.latched) {
    let t = est.by_robot_only ? 'Robot tarafında ACİL DUR aktif (başka bir istemci ya da düğme). '
                              : 'ACİL DUR aktif: senaryolar kilitli. ';
    if (est.pending) t += 'Robotun durduğu doğrulanıyor…';
    else if (rep && rep.stopped === true) {
      const sec = rep.details && rep.details.stop_time_s;
      t += `Robotun durduğu doğrulandı${sec != null ? ` (${sec} sn)` : ''}. Güvenliyse “Kilidi aç”a bas.`;
    } else if (rep && rep.stopped !== true) {
      t = `⚠ ROBOTUN DURDUĞU DOĞRULANAMADI: ${rep.message} Fiziksel acil durdurmayı kullan!`;
    } else t += 'Güvenliyse “Kilidi aç”a bas.';
    show(rep && rep.stopped === false ? 'danger alarm' : 'danger', t);
  } else if (rb.compatible === false && rb.connected) show('', `Robot API uyumsuz: ${rb.problem}`);
  else if (rb.connected === false) show('', `Robot bağlı değil: ${rb.problem || 'yanıt yok'}. Hareket komutları çalışmaz.`);
  else if (readOnly) show('', 'Kontroller kilitli (salt okunur kip). ACİL DUR yine de çalışır.');
  else if (rb.mode === 'real') show('', 'Gerçek robot: bu yazılım acil durdurur; fiziksel acil durdurmanın yerine geçmez.');
  else banner.hidden = true;

  const bar = $('estop-bar'); bar.classList.toggle('latched', s.estop.latched);
  $('estop-label').textContent = s.estop.latched ? 'ACİL DUR AKTİF' : 'ACİL DUR';
  const reset = $('btn-reset'); reset.hidden = !s.estop.latched; reset.disabled = !s.me.can_control;
  HubPages.onState(s);
}

/* ---------- canlı akış ---------- */
function closeWs() { if (S.ws) { S.ws.onclose = null; S.ws.close(); S.ws = null; } S.wsOk = false; stopPoll(); }

function connectWs() {
  closeWs(); setConn('connecting');
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  S.ws = ws;
  ws.onopen = () => { S.wsOk = true; S.retry = 0; stopPoll(); setConn('live'); };
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    S.liveAt = performance.now();                 // durum da, tick de: canlılık işareti (3B görünüm bayatlığı)
    if (d.type === 'state') { S.snap = d.state; render(); }
    else if (d.type === 'events') { S.events.push(...d.events); S.events = S.events.slice(-200); HubPages.onEvents(S.events); }
  };
  ws.onclose = (ev) => {
    S.wsOk = false; S.ws = null;
    if (ev.code === 4401) { showLogin(); return; }
    setConn('down'); startPoll();
    S.retry = Math.min(S.retry + 1, 5); setTimeout(() => { if (!S.ws) connectWs(); }, 1000 * S.retry);
  };
}
function startPoll() {   // WS yokken durum yine de yenilensin
  if (S.pollTimer) return;
  S.pollTimer = setInterval(async () => { try { S.snap = await api('/api/state'); render(); } catch (e) { /* bağlantı yok */ } }, 2000);
}
function stopPoll() { if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; } }

init();
