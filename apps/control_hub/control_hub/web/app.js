/* Robot Kontrol Merkezi: düz JS, derleme yok. Veri her zaman textContent ile yazılır (innerHTML yok). */
'use strict';

const $ = (id) => document.getElementById(id);
const S = { session: null, ui: null, snap: null, ws: null, wsOk: false, events: [], armed: null,
            buttons: {}, retry: 0, pollTimer: null };

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
  try {
    S.session = await api('/api/session');
  } catch (e) { setConn('down'); toast(e.message, true); setTimeout(init, 3000); return; }
  applyTheme(S.session.theme);
  $('title').textContent = S.session.title; document.title = S.session.title;
  if (S.session.mode === 'pin' && !S.session.authenticated) showLogin(); else start();
}

function showLogin() {
  $('main').hidden = true; $('login').hidden = false; $('btn-logout').hidden = true;
  closeWs(); $('pin').value = ''; $('pin').focus(); setConn('down', 'Giriş gerekli');
}

async function onLogin(ev) {
  ev.preventDefault();
  $('login-error').textContent = '';
  try {
    await api('/api/login', 'POST', { pin: $('pin').value.trim() });
    S.session = await api('/api/session');
    start();
  } catch (e) {
    if (e.status === 429) $('login-error').textContent = `Çok fazla deneme. ${Math.ceil(e.body.retry_after_s || 60)} sn bekle.`;
    else if (e.status === 401) $('login-error').textContent = `PIN hatalı (${e.body.attempts_left} deneme hakkın kaldı).`;
    else $('login-error').textContent = e.message;
    $('pin').select();
  }
}

async function logout() { try { await api('/api/logout', 'POST'); } catch (e) { /* yok say */ } closeWs(); init(); }

async function start() {
  $('login').hidden = true;
  try {
    S.ui = await api('/api/ui');
    S.snap = await api('/api/state');
  } catch (e) { toast(e.message, true); return; }
  buildUi();
  $('main').hidden = false;
  render();
  connectWs();
}

/* ---------- arayüz iskeleti (yapılandırmadan) ---------- */
function buildUi() {
  const lay = S.ui.layout;
  applyTheme(S.ui.theme);
  document.documentElement.style.setProperty('--btn-h', lay.button_height);
  const pos = lay.estop.position;
  document.body.dataset.estopPos = pos;
  const bar = $('estop-bar'); bar.dataset.pos = pos;
  if (pos === 'top') $('estop-top-slot').append(bar); else document.body.append(bar);

  const secs = $('sections'); secs.replaceChildren(); S.buttons = {};
  for (const sec of lay.sections) {
    const grid = el('div', { class: 'grid', style: { '--cols': String(sec.columns) } });
    for (const id of sec.scenarios) {
      const sc = S.ui.scenarios[id];
      const b = el('button', { class: 'scn', type: 'button', 'data-id': id,
        style: sc.color ? { '--c': sc.color } : {} }, [
        el('span', { class: 'ic', text: sc.icon || '▶' }),
        el('span', { class: 'lb', text: sc.label }),
        el('span', { class: 'ds', text: sc.description }),
        el('span', { class: 'bar' }),
      ]);
      b.addEventListener('click', () => onScenario(id));
      S.buttons[id] = b; grid.append(b);
    }
    secs.append(el('section', { class: 'group' }, [sec.title ? el('h2', { text: sec.title }) : '', grid]));
  }

  const panels = $('panels'); panels.replaceChildren();
  const builders = {
    status: () => el('div', { class: 'card', id: 'p-status' }, [
      el('h2', { text: 'Durum' }), el('div', { class: 'status-big', id: 'st-big' }),
      el('div', { class: 'say', id: 'st-say' }), el('div', { class: 'say', id: 'st-last' }),
      el('div', { class: 'row' }, [el('button', { class: 'small', id: 'btn-cancel', type: 'button',
        text: 'Senaryoyu iptal et' })])]),
    robot: () => el('div', { class: 'card', id: 'p-robot' }, [
      el('h2', { text: 'Robot' }), el('dl', { class: 'kv', id: 'rb-kv' }), el('div', { class: 'joints', id: 'rb-joints' })]),
    log: () => el('div', { class: 'card', id: 'p-log' }, [el('h2', { text: 'Olay günlüğü' }), el('ul', { class: 'log', id: 'lg' })]),
  };
  for (const p of lay.panels) panels.append(builders[p]());
  const c = $('btn-cancel'); if (c) c.addEventListener('click', onCancel);
  S.events = [];
  api('/api/events').then((r) => { S.events = r.events; renderLog(); }).catch(() => {});
}

/* ---------- eylemler ---------- */
async function onScenario(id) {
  const sc = S.ui.scenarios[id];
  if (sc.confirm && S.armed !== id) {
    S.armed = id; render(); setTimeout(() => { if (S.armed === id) { S.armed = null; render(); } }, 3000); return;
  }
  S.armed = null;
  try { await api(`/api/scenarios/${id}/run`, 'POST'); } catch (e) { toast(e.message, true); }
  render();
}
async function onCancel() { try { await api('/api/scenarios/cancel', 'POST'); } catch (e) { toast(e.message, true); } }
async function onEstop() {
  // Tek dokunuş, onay yok. Ağ hatasında da tekrar dene: durdurma sessizce başarısız olmamalı.
  for (let i = 0; i < 3; i++) {
    try {
      const r = await api('/api/estop', 'POST');
      if (r && r.interrupts_motion === false) toast('Dikkat: bu robot hareketi ortasında kesilemiyor; sonraki adımlar iptal edildi.', true);
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
function setConn(state, label) {
  const p = $('pill-conn'); p.dataset.state = state;
  p.textContent = label || ({ live: 'Canlı', down: 'Bağlantı yok', connecting: 'Bağlanıyor…' })[state];
}
const fmt = (v) => (v == null ? '—' : v.toFixed(3));
const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString('tr-TR', { hour12: false });

function render() {
  const s = S.snap; if (!s || !S.ui) return;
  const canControl = s.me.can_control && !s.estop.latched;
  const run = s.runner;
  const readOnly = s.access.mode === 'locked';

  const mode = $('pill-mode');
  mode.hidden = !readOnly; mode.textContent = 'Salt okunur'; mode.className = 'pill warn';
  $('btn-logout').hidden = s.access.mode !== 'pin';
  const banner = $('banner');
  if (s.estop.latched) { banner.hidden = false; banner.className = 'banner danger';
    banner.textContent = 'ACİL DUR aktif: senaryolar kilitli. Güvenliyse “Kilidi aç”a bas.'; }
  else if (readOnly) { banner.hidden = false; banner.className = 'banner';
    banner.textContent = 'Kontroller kilitli (salt okunur kip). ACİL DUR yine de çalışır.'; }
  else if (s.robot && s.robot.connected === false) { banner.hidden = false; banner.className = 'banner';
    banner.textContent = 'Robot bağlı değil: butonlar çalışmaz.'; }
  else banner.hidden = true;

  for (const [id, b] of Object.entries(S.buttons)) {
    const mine = run.running && run.scenario === id;
    b.classList.toggle('running', mine);
    b.classList.toggle('armed', S.armed === id);
    b.disabled = !canControl || run.running || (s.robot && s.robot.connected === false);
    if (mine) b.disabled = true;
    b.querySelector('.ds').textContent = S.armed === id ? 'Emin misin? Tekrar dokun.' : S.ui.scenarios[id].description;
    b.querySelector('.bar').style.width = mine && run.steps_total ? `${Math.round(((run.step - 1) / run.steps_total) * 100)}%` : '0';
  }

  const bar = $('estop-bar'); bar.classList.toggle('latched', s.estop.latched);
  $('estop-label').textContent = s.estop.latched ? 'ACİL DUR AKTİF' : 'ACİL DUR';
  const reset = $('btn-reset'); reset.hidden = !s.estop.latched; reset.disabled = !s.me.can_control;

  const big = $('st-big');
  if (big) {
    let text = 'Hazır', cls = 'ok';
    if (s.estop.latched) { text = 'ACİL DUR'; cls = 'bad'; }
    else if (run.running) { text = `${run.label} (adım ${run.step}/${run.steps_total})`; cls = 'busy'; }
    big.textContent = text; big.className = 'status-big ' + cls;
    $('st-say').textContent = run.running ? run.say : '';
    const last = run.last;
    $('st-last').textContent = !run.running && last ? `Son: ${last.label}: ${last.message}` : '';
    const c = $('btn-cancel'); c.disabled = !run.running || !s.me.can_control; c.hidden = !run.running;
  }

  const kv = $('rb-kv');
  if (kv && s.robot) {
    const r = s.robot;
    kv.replaceChildren(...[['Arka uç', r.name], ['Bağlantı', r.connected ? 'bağlı' : 'yok'],
      ['Hareket', r.moving ? 'hareket ediyor' : 'duruyor'],
      ['Uç konum (m)', r.tool ? r.tool.map(fmt).join('  ') : '—']]
      .flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: String(v) })]));
    $('rb-joints').replaceChildren(...(r.joints || []).map((j) => el('div', { class: 'joint' }, [
      el('b', { text: j.deg == null ? '—' : `${j.deg.toFixed(1)}°` }), el('span', { text: j.name })])));
  }
  renderLog();
}

function renderLog() {
  const ul = $('lg'); if (!ul) return;
  ul.replaceChildren(...S.events.slice(-40).reverse().map((e) =>
    el('li', { 'data-level': e.level }, [el('time', { text: hhmmss(e.ts) }), el('span', { text: e.message })])));
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
    if (d.type === 'state') { S.snap = d.state; render(); }
    else if (d.type === 'events') { S.events.push(...d.events); S.events = S.events.slice(-200); renderLog(); }
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
