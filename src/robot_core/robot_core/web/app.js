'use strict';
const REFRESH_MS = 200;
const $ = (id) => document.getElementById(id);
const fmt = (v) => v.map((x) => x.toFixed(3)).join(', ');
const urls = { rgb: null, depth: null };

async function refreshImage(name, path) {
  const frame = $(name).parentElement;
  try {
    const r = await fetch(path + '?t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) { frame.classList.remove('live'); $(name + '-msg').textContent = 'kare yok (' + r.status + ')'; return; }
    const url = URL.createObjectURL(await r.blob());
    $(name).src = url;
    if (urls[name]) URL.revokeObjectURL(urls[name]);
    urls[name] = url;
    frame.classList.add('live');
  } catch (e) { frame.classList.remove('live'); $(name + '-msg').textContent = 'bağlantı yok'; }
}

function fill(tbody, rows) {
  tbody.replaceChildren(...rows.map((cells) => {
    const tr = document.createElement('tr');
    cells.forEach((c) => { const td = document.createElement('td'); td.textContent = c; tr.appendChild(td); });
    return tr;
  }));
}

async function refreshState() {
  const badge = $('status');
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    const s = await r.json();
    fill($('objects').tBodies[0], s.objects.map((o) => [o.name, fmt(o.position), fmt(o.size)]));
    fill($('joints').tBodies[0], (s.joints || []).map((j) => [j.name, j.rad.toFixed(3), j.deg.toFixed(1)]));
    $('tool-link').textContent = s.tool ? s.tool.link : '-';
    $('tool').textContent = s.tool ? 'x, y, z = ' + fmt(s.tool.position) + ' m' : 'TF yok';
    const age = s.camera.rgb_age_s;
    const fresh = age !== null && age < 2;
    badge.textContent = fresh ? 'canlı' : 'kamera akmıyor';
    badge.className = 'badge ' + (fresh ? 'ok' : 'bad');
  } catch (e) { badge.textContent = 'sunucuya ulaşılamıyor'; badge.className = 'badge bad'; }
}

async function tick() {
  await Promise.all([refreshImage('rgb', '/frame/rgb.png'), refreshImage('depth', '/frame/depth.png'), refreshState()]);
  setTimeout(tick, REFRESH_MS);
}
tick();
