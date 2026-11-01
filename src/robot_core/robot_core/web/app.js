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

const FOOTER_READ_ONLY = $('footer').textContent;
const FOOTER_COMMANDS = 'Komutlar açık: yalnızca beyaz listedeki komutlar çalışır '
  + '(home, objects, above, demo_scenario); aynı anda tek komut. Yenileme ~5 Hz.';
let busy = false;
let jobId = null;            // the job this page started (its result is shown when it ends)

function setBusy(running) {
  busy = running;
  document.querySelectorAll('#commands button:not(.cancel)').forEach((b) => { b.disabled = running; });
  $('cancel').disabled = !running;
}

async function showJobResult(id) {
  const msg = $('cmd-msg');
  try {
    const j = await (await fetch('/api/jobs/' + id, { cache: 'no-store' })).json();
    const text = (j.result && j.result.message) || j.error || j.state;
    const ok = j.state === 'done';
    msg.className = ok ? 'msg' : 'msg err';
    msg.textContent = (ok ? '[+] ' : '[-] (' + j.state + ') ') + text;
  } catch (e) { msg.className = 'msg err'; msg.textContent = '[-] iş sonucu okunamadı'; }
}

async function sendCommand(line) {
  const msg = $('cmd-msg');
  msg.className = 'msg';
  msg.textContent = '> ' + line + ' ...';
  setBusy(true);
  try {
    const r = await fetch('/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: line }),
    });
    const j = await r.json();
    if (r.status === 202) { jobId = j.id; }
    else {
      msg.className = 'msg err';
      msg.textContent = '[-] (' + r.status + ') ' + (j.error || ('HTTP ' + r.status));
    }
  } catch (e) {
    msg.className = 'msg err';
    msg.textContent = '[-] sunucuya ulaşılamadı';
  }
  refreshState();
}

$('cancel').addEventListener('click', async () => {
  if (!jobId) return;
  try { await fetch('/api/jobs/' + jobId + '/cancel', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch (e) { /* state shows it */ }
  refreshState();
});

async function postEmpty(path, body) {
  const msg = $('estop-msg');
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body });
    const j = await r.json();
    msg.className = r.ok ? 'msg' : 'msg err';
    msg.textContent = j.message || j.error || ('HTTP ' + r.status);
  } catch (e) { msg.className = 'msg err'; msg.textContent = 'sunucuya ulaşılamadı'; }
  refreshState();
}
$('estop').addEventListener('click', () => postEmpty('/api/stop', JSON.stringify({ reason: 'web sayfası' })));
$('reset').addEventListener('click', () => postEmpty('/api/reset', '{}'));

const STATE_TEXT = { idle: 'hazır', busy: 'çalışıyor', estopped: 'ACİL DUR', error: 'hata' };

document.querySelectorAll('[data-cmd]').forEach((b) =>
  b.addEventListener('click', () => sendCommand(b.dataset.cmd)));
document.querySelectorAll('[data-above]').forEach((b) =>
  b.addEventListener('click', () => sendCommand('above ' + b.dataset.above + ' ' + $('height').value)));

async function refreshState() {
  const badge = $('status');
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    const s = await r.json();
    fill($('objects').tBodies[0], s.objects.map((o) => [o.name, fmt(o.position), fmt(o.size)]));
    fill($('joints').tBodies[0], (s.joints || []).map((j) => [j.name, j.rad.toFixed(3), j.deg.toFixed(1)]));
    $('tool-link').textContent = s.tool ? s.tool.link : '-';
    $('tool').textContent = s.tool ? 'x, y, z = ' + fmt(s.tool.position) + ' m' : 'TF yok';
    const enabled = s.commands.enabled;
    $('commands').hidden = !enabled;
    $('estop-bar').hidden = !enabled;
    if (enabled && s.robot) {
      const st = s.robot.state;
      $('robot-state').textContent = (STATE_TEXT[st] || st) + (s.robot.moving ? ' - hareket ediyor' : '');
      $('robot-state').className = 'badge ' + (st === 'idle' ? 'ok' : 'bad');
      $('reset').disabled = st !== 'estopped' && st !== 'error';
    }
    $('footer').textContent = enabled ? FOOTER_COMMANDS : FOOTER_READ_ONLY;
    if (enabled) {
      setBusy(s.command.running);
      if (jobId && !s.command.running) { const id = jobId; jobId = null; showJobResult(id); }
      if (s.command.log.length) $('cmd-log').textContent = s.command.log.join('\n');
    }
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
