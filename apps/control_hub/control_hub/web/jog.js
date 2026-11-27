/* Jog motoru + kartezyen jog kartı (P1; app.js ve jog_logic.js'ten sonra yüklenir). Veri textContent ile yazılır.
 * Kipler: ADIM (varsayılan; tek tık = tek adım 0.1/1/10 mm ya da °) ve SÜREKLİ (basılı-tut; yalnız sunucu izin
 * verirse ve bu bilgisayardan). Bırakınca, sekme gizlenince, odak kaybında ya da bağlantı kopunca DURUR:
 * her komut heartbeat'tir (100 ms), asıl dead-man SUNUCUDA (TTL 250 ms; JointHold / robot jog). Yazılım ACİL DUR
 * ve dead-man donanım acil durdurmanın yerine geçmez. Eklem ± ve "Git/Home/Alet" aynı motoru kullanır. */
'use strict';

window.JogPanel = (() => {
  const L = window.JogLogic;
  const J = { mounted: false, host: null, st: null, lease: newLease(), step: 1, hold: false, frame: 'World',
              speed: 50, busy: false, beat: null, held: null, holdBlocked: null, snap: null };
  const q = (id) => document.getElementById(id);

  function newLease() {
    const b = new Uint8Array(12); crypto.getRandomValues(b);
    return 'hub-' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  }
  function say(text, bad = false) { HubPages.say(q('jp-msg'), text, bad); }
  const post = (body) => api('/api/jog', 'POST', { lease: J.lease, frame: J.frame, ...body });

  /* ---- ortak: eklem hedefine git (önce kuru kontrol, sonra hareket) ---- */
  async function goJoints(targets) {
    const body = L.moveBody(targets, J.speed);
    let check;
    try { check = await api('/api/move/check', 'POST', body); } catch (e) { return { ok: false, text: L.explain(e) }; }
    const v = L.checkVerdict(check);
    if (!v.ok) return v;                                     // ihlal varsa hareket komutu HİÇ gitmez
    try { await api('/api/move', 'POST', body); } catch (e) { return { ok: false, text: L.explain(e) }; }
    return { ok: true, text: 'Hareket başladı.' };
  }
  async function setGripper(pct) {
    try { await api('/api/move', 'POST', { type: 'tool', open_pct: pct, speed_pct: J.speed }); return { ok: true, text: '' }; }
    catch (e) { return { ok: false, text: L.explain(e) }; }
  }
  async function stopMotion() {
    try { await api('/api/move/stop', 'POST', {}); } catch (e) { if (e.status !== 409) say(L.explain(e), true); }
  }

  /* ---- bağlama ---- */
  function mount(host) {
    if (J.mounted) return;
    if (host) J.host = host;
    api('/api/jog').then((st) => {
      if (!st || !st.enabled) return unavailable('robot jog\'u kapalı bildirdi (enabled=false)');
      const old = q('p-jog'); if (old) old.remove();
      J.st = st; J.mounted = true; build(); refreshFrames();
      if (window.FrameWizard) FrameWizard.mount(J.host);
      if (J.snap) onState(J.snap); else apply();
    }).catch((e) => {
      const old = e && (e.status === 404 || e.status === 501);
      unavailable(L.explain(e) + (old ? ' — robot yığını eski olabilir: WSL\'de `docker compose build` sonra yeniden başlat' : ''));
    });
  }
  function unavailable(reason) {
    const prev = q('p-jog'); if (prev) prev.remove();
    const retry = el('button', { class: 'small', type: 'button', text: 'Yeniden dene' });
    retry.addEventListener('click', () => { const c = q('p-jog'); if (c) c.remove(); mount(J.host); });
    J.host.append(el('div', { class: 'card', id: 'p-jog' }, [
      el('h2', { text: 'Jog (elle hareket)' }),
      el('div', { class: 'say bad', id: 'jg-off', text: `Jog kullanılamıyor: ${reason}` }),
      el('div', { class: 'row' }, [retry])]));
  }

  function build() {
    const grid = el('div', { class: 'jp-grid', id: 'jp-grid' });
    for (const [name, unit, i] of L.AXES) {
      grid.append(el('div', { class: 'jp-ax', text: `${name} (${unit})` }), axisBtn(i, -1),
        el('output', { class: 'jp-val', id: `jp-v${i}`, text: '—' }), axisBtn(i, +1));
    }
    const modeBtn = (hold, text) => {
      const b = el('button', { class: 'small jp-mode', type: 'button', text, 'data-hold': hold ? '1' : '0' });
      b.addEventListener('click', () => { if (!hold || holdAllowed()) { stopHold(); J.hold = hold; apply(); } });
      return b;
    };
    const speed = el('input', { type: 'range', id: 'jp-speed', min: String(L.SPEED_MIN), max: String(L.SPEED_MAX), step: '1',
                                value: String(J.speed), 'aria-label': 'Hız yüzdesi' });
    speed.addEventListener('input', () => { J.speed = L.clampPct(speed.value); q('jp-speed-v').textContent = `%${J.speed}`; });
    J.host.append(el('div', { class: 'card', id: 'p-jog' }, [
      el('h2', { text: 'Jog (elle hareket)' }),
      el('div', { class: 'jp-bar' }, [
        el('div', { class: 'jp-seg', role: 'group', 'aria-label': 'Jog kipi' }, [modeBtn(false, 'Adım'), modeBtn(true, 'Sürekli')]),
        el('div', { class: 'jp-seg', id: 'jp-steps', role: 'group', 'aria-label': 'Adım büyüklüğü (mm ya da °)' })]),
      el('div', { class: 'row' }, [
        el('label', { class: 'jp-f', text: 'Çerçeve ' }, [el('select', { id: 'jg-frame', 'aria-label': 'Çerçeve' })]),
        el('label', { class: 'jp-f jp-speed', text: 'Hız ' }, [speed, el('output', { id: 'jp-speed-v', text: `%${J.speed}` })])]),
      grid,
      el('div', { class: 'say', id: 'jp-why' }),
      el('div', { class: 'row' }, [el('button', { class: 'small', id: 'jp-release', type: 'button', text: 'Jog’u bırak' }),
                                   el('button', { class: 'small', id: 'jp-stop', type: 'button', text: 'Hareketi durdur' })]),
      el('div', { class: 'say jp-msg', id: 'jp-msg', role: 'alert' }),
      el('div', { class: 'say', id: 'jg-info' })]));
    for (const s of L.STEPS) {
      const b = el('button', { class: 'small jp-step', type: 'button', text: String(s), 'data-step': String(s) });
      b.addEventListener('click', () => { J.step = s; apply(); });
      q('jp-steps').append(b);
    }
    q('jg-frame').addEventListener('change', (ev) => { J.frame = ev.target.value; });
    q('jp-release').addEventListener('click', () => release(true));
    q('jp-stop').addEventListener('click', () => { stopHold(); stopMotion(); });
    window.addEventListener('blur', () => stopHold());
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopHold(); });
    window.addEventListener('pagehide', () => {
      for (const url of ['/api/jog', '/api/jog/joint']) {
        try { navigator.sendBeacon(url, new Blob([JSON.stringify({ lease: J.lease, ttl_ms: 0 })], { type: 'application/json' })); }
        catch (e) { /* yok say: sunucu TTL'i zaten durdurur */ }
      }
    });
  }

  /* Basılı-tut/adım bağlayıcı: hem kartezyen hem eklem düğmeleri bunu kullanır. */
  function bindHoldButton(b, onStep, onHoldStart) {
    b.addEventListener('click', (ev) => { if (!J.hold) onStep(); });
    b.addEventListener('pointerdown', (ev) => {
      if (J.hold && ev.button === 0) { b.setPointerCapture(ev.pointerId); onHoldStart(); }
    });
    for (const t of ['pointerup', 'pointercancel', 'lostpointercapture']) b.addEventListener(t, () => stopHold());
    b.addEventListener('contextmenu', (ev) => ev.preventDefault());
  }
  const axisBtn = (i, sign) => {
    const b = el('button', { class: 'jp-b', type: 'button', 'data-axis': String(i), 'data-sign': String(sign),
                             text: sign < 0 ? '−' : '+', 'aria-label': `${L.AXES[i][0]} ${sign < 0 ? 'eksi' : 'artı'}` });
    bindHoldButton(b, () => cartStep(i, sign), () => startHold({ url: '/api/jog', body: L.holdBody(i, sign, J.speed) }));
    return b;
  };
  function jointButton(i, sign, text) {
    const b = el('button', { class: 'jp-jb', type: 'button', text, 'data-joint': String(i), 'data-sign': String(sign),
                             'aria-label': `Eklem ${i + 1} ${sign < 0 ? 'eksi' : 'artı'}` });
    bindHoldButton(b, () => jointStep(i, sign), () => startHold({ url: '/api/jog/joint',
      body: { joint: i, dir: sign, speed_pct: J.speed } }));
    return b;
  }

  /* ---- adım modu ---- */
  async function cartStep(axis, sign) {
    if (J.busy) return;
    J.busy = true; apply();
    try { await post({ increment: { axis, amount: sign * J.step }, ttl_ms: L.TTL_MS }); say(''); }
    catch (e) { say(L.explain(e), true); } finally { J.busy = false; apply(); }
  }
  async function jointStep(i, sign) {
    if (J.busy) return;
    const joints = (J.snap && J.snap.robot && J.snap.robot.joints) || [];
    if (!joints[i]) return say('Eklem durumu yok.', true);
    J.busy = true; apply();
    const targets = joints.map((j) => j.deg);
    targets[i] = L.stepTarget(targets[i], sign, J.step);
    const r = await goJoints(targets);
    say(r.ok ? '' : r.text, !r.ok); J.busy = false; apply();
  }

  /* ---- sürekli (basılı-tut): her 100 ms bir komut; önceki bitmediyse atlanır (kuyruk birikmez) ---- */
  function startHold(spec) {
    stopHold(true);
    J.held = { spec, inflight: false };
    const beat = async () => {
      const h = J.held; if (!h || h.inflight) return;
      h.inflight = true;
      try {
        const doc = await api(spec.url, 'POST', { lease: J.lease, frame: J.frame, ...spec.body, ttl_ms: L.TTL_MS });
        say('');
        if (doc && doc.state === 'at_limit') stopHold(true);          // eklem limite vardı
      } catch (e) {
        say(L.explain(e), true);
        if (e.status === 403) blockHold(e.body && (e.body.message || e.body.error) || 'sunucu reddetti');
        stopHold(true);
      } finally { h.inflight = false; }
    };
    beat(); J.beat = setInterval(beat, L.BEAT_MS);
  }
  function stopHold(quiet) {
    if (J.beat) { clearInterval(J.beat); J.beat = null; }
    const h = J.held; J.held = null;
    if (h && !quiet) {                       // bırakma = ttl_ms:0; kaybolursa sunucu TTL'i zaten durdurur
      api(h.spec.url, 'POST', { lease: J.lease, ttl_ms: 0 }).catch((e) => { if (e.status !== 423) say(L.explain(e), true); });
    }
  }
  function blockHold(reason) { J.holdBlocked = reason; J.hold = false; apply(); }
  function holdAllowed() { return !!(J.st && J.st.hold_allowed) && !J.holdBlocked; }

  async function release(loud) {
    stopHold(true);
    try {
      await api('/api/jog', 'POST', { lease: J.lease, ttl_ms: 0 });
      await api('/api/jog/joint', 'POST', { lease: J.lease, ttl_ms: 0 }).catch(() => null);
      if (loud) say('Jog bırakıldı.');
    } catch (e) { if (loud || e.status !== 423) say(L.explain(e), true); }
  }

  /* ---- çerçeveler: Dünya, Alet ve öğretilmiş kullanıcı çerçeveleri ---- */
  async function refreshFrames() {
    try { const d = await api('/api/frames'); J.frames = ['World', 'Tool', ...d.frames.map((f) => f.name)]; }
    catch (e) { J.frames = ['World', 'Tool']; }
    const sel = q('jg-frame'); if (!sel) return;
    sel.replaceChildren();
    const label = { World: 'Dünya', Tool: 'Alet' };
    for (const n of J.frames) sel.append(el('option', { value: n, text: label[n] || n }));
    if (!J.frames.includes(J.frame)) J.frame = 'World';
    sel.value = J.frame;
  }

  /* ---- görünüm ---- */
  function apply() {
    if (!J.mounted) return;
    const s = J.snap, why = L.disabledReason(s);
    const lock = !!why || (J.busy && !J.hold);
    for (const b of document.querySelectorAll('#jp-grid .jp-b')) b.disabled = lock;
    for (const b of document.querySelectorAll('.jp-jb')) b.disabled = lock;
    for (const b of document.querySelectorAll('.jp-step')) b.classList.toggle('on', Number(b.dataset.step) === J.step);
    for (const b of document.querySelectorAll('.jp-mode')) b.classList.toggle('on', (b.dataset.hold === '1') === J.hold);
    const cont = document.querySelector('.jp-mode[data-hold="1"]'); if (cont) cont.disabled = !holdAllowed() || !!why;
    q('jp-release').disabled = !!(s && s.me && !s.me.can_control);
    q('jp-why').textContent = why || (holdAllowed() ? '' : `Sürekli kip kapalı: ${J.holdBlocked || (J.st && J.st.hold_reason) || 'bilinmiyor'}`);
    q('jp-grid').classList.toggle('lock', lock);
    q('jp-grid').dataset.mode = J.hold ? 'hold' : 'step';
  }
  function onState(s) {
    J.snap = s;
    if (!J.mounted) return;
    if (window.FrameWizard) FrameWizard.onState(s);
    const r = s.robot || {}, row = L.poseRows(r);
    if (row.tcp) { row.tcp.mm.forEach((v, i) => { q(`jp-v${i}`).textContent = v; });
                   row.tcp.deg.forEach((v, i) => { q(`jp-v${i + 3}`).textContent = v; }); }
    const jg = r.jog;
    q('jg-info').textContent = jg ? `Jog: ${jg.mode || '—'} · çerçeve ${jg.frame || '—'} · durum ${jg.status}` +
      (jg.messages && jg.messages.length ? ` · ${jg.messages.join('; ')}` : '') : (r.state ? `Robot: ${r.state}` : '');
    apply();
  }
  /* Hız tek kaynağı (P1e): kaydırıcı 1–100'ü gösterir (uca yaslanır), değer ise 100'ün üstüne çıkabilir (SİM). */
  function setSpeed(v) {
    J.speed = L.clampPct(v);
    const sl = q('jp-speed'), out = q('jp-speed-v');
    if (sl) { sl.step = '0.1'; sl.value = String(Math.min(L.SPEED_MAX, J.speed)); }
    if (out) out.textContent = `%${J.speed}`;
  }
  function setFrame(name) { J.frame = name; const sel = q('jg-frame'); if (sel) sel.value = name; }
  const tcp = () => (J.snap && J.snap.robot && J.snap.robot.tcp) || null;
  return { mount, onState, explain: L.explain, release, refreshFrames, setFrame, tcp, frame: () => J.frame,
           goJoints, setGripper, stopMotion, jointButton, say, speed: () => J.speed, setSpeed, stopHold };
})();
