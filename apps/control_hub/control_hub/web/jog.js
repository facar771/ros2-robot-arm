/* Jog paneli (J3; app.js'ten sonra yüklenir). Veri textContent ile yazılır (innerHTML yok).
 * Varsayılan ARTIMLI: tek tık = tek adım (0.1 / 1 / 10 mm ya da °). Basılı-tut yalnızca sunucu izin verirse
 * (GET /api/jog -> hold_allowed) ve kullanıcı açıkça seçerse; tuş bırakılınca/sekme gizlenince/odak kaybında
 * anında bırakılır. Her komut heartbeat'tir (100 ms); asıl dead-man SUNUCUDA (TTL), bu kod yalnızca kibar
 * davranır. Yazılım ACİL DUR/dead-man donanım acil durdurmanın yerine geçmez. */
'use strict';

window.JogPanel = (() => {
  const AX = [['X', 'mm', 0], ['Y', 'mm', 1], ['Z', 'mm', 2], ['W', '°', 3], ['P', '°', 4], ['R', '°', 5]];
  const STEPS = [0.1, 1, 10];
  const HOLD_LIN = 0.02, HOLD_ANG = 0.10, BEAT_MS = 100, TTL_MS = 250;   // sunucu zaten kırpar; dar tutuyoruz
  const J = { mounted: false, st: null, lease: newLease(), step: 1, hold: false, frame: 'World',
              frames: [], busy: false, beat: null, held: null, holdBlocked: null, snap: null, msg: null };
  const q = (id) => document.getElementById(id);

  function newLease() {
    const b = new Uint8Array(12); crypto.getRandomValues(b);
    return 'hub-' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  }

  /* Hata kodlarını net metne çevirir (409 başka istemci/iş, 423 e-stop, 403 izin, 502 robot yok). */
  function explain(e) {
    const m = (e.body && (e.body.message || e.body.error)) || e.message;
    switch (e.status) {
      case 409: return `Başka bir istemci jog yapıyor ya da robot meşgul (409): ${m}`;
      case 423: return `ACİL DUR kilidinde (423): önce kilidi aç. ${m}`;
      case 403: return `İzin yok (403): ${m}`;
      case 502: return `Robota ulaşılamıyor (502): ${m}`;
      case 503: return `Robot eklem durumu yok (503): ${m}`;
      case 0: return 'Hub’a ulaşılamıyor';
      default: return `${m} (HTTP ${e.status})`;
    }
  }
  function say(text, bad = false) {
    J.msg = text ? { text, bad } : null;
    const n = q('jg-msg'); if (!n) return;
    n.textContent = text || ''; n.classList.toggle('bad', !!bad);
  }

  async function post(body) { return api('/api/jog', 'POST', { lease: J.lease, frame: J.frame, ...body }); }

  function mount() {
    if (J.mounted) return;
    api('/api/jog').then((st) => {
      if (!st || !st.enabled) return;            // robotta jog kapalı: panel çizilmez
      J.st = st; J.mounted = true; build(); refreshFrames();
      if (window.FrameWizard) FrameWizard.mount();
      if (J.snap) onState(J.snap); else apply();   // ilk durum mount'tan önce gelmiş olabilir
    }).catch(() => { /* jog yok (403/501/502): panel hiç görünmez */ });
  }

  function build() {
    const grid = el('div', { class: 'jg-grid', id: 'jg-grid' });
    for (const [name, unit, i] of AX) {
      grid.append(el('div', { class: 'jg-ax', text: `${name} (${unit})` }),
        axisBtn(i, -1), el('output', { class: 'jg-val', id: `jg-v${i}`, text: '—' }), axisBtn(i, +1));
    }
    q('panels').append(el('div', { class: 'card', id: 'p-jog' }, [
      el('h2', { text: 'Jog (elle hareket)' }),
      el('div', { class: 'row' }, [
        el('label', { class: 'jg-f', text: 'Çerçeve ' }, [el('select', { id: 'jg-frame', 'aria-label': 'Çerçeve' })]),
        el('div', { class: 'jg-steps', id: 'jg-steps', role: 'group', 'aria-label': 'Adım' })]),
      grid,
      el('label', { class: 'jg-hold' }, [
        el('input', { type: 'checkbox', id: 'jg-hold' }), document.createTextNode(' Basılı-tut modu (tuşa basılı tutarken hareket)')]),
      el('div', { class: 'say', id: 'jg-why' }),
      el('div', { class: 'row' }, [
        el('button', { class: 'small', id: 'jg-ready', type: 'button', text: 'Hazır pozuna git' }),
        el('button', { class: 'small', id: 'jg-release', type: 'button', text: 'Jog’u bırak' })]),
      el('div', { class: 'say jg-msg', id: 'jg-msg', role: 'alert' }),
      el('div', { class: 'say', id: 'jg-info' })]));
    for (const s of STEPS) {
      const b = el('button', { class: 'small jg-step', type: 'button', text: String(s), 'data-step': String(s) });
      b.addEventListener('click', () => { J.step = s; apply(); });
      q('jg-steps').append(b);
    }
    q('jg-frame').addEventListener('change', (ev) => { J.frame = ev.target.value; });
    q('jg-hold').addEventListener('change', (ev) => { J.hold = ev.target.checked; stopHold(); apply(); });
    q('jg-release').addEventListener('click', () => release(true));
    q('jg-ready').addEventListener('click', onReady);
    window.addEventListener('blur', () => stopHold());
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopHold(); });
    window.addEventListener('pagehide', () => {
      try { navigator.sendBeacon('/api/jog', new Blob([JSON.stringify({ lease: J.lease, ttl_ms: 0 })],
        { type: 'application/json' })); } catch (e) { /* yok say */ }
    });
  }

  function axisBtn(i, sign) {
    const b = el('button', { class: 'jg-b', type: 'button', 'data-axis': String(i), 'data-sign': String(sign),
      text: sign < 0 ? '−' : '+', 'aria-label': `${AX[i][0]} ${sign < 0 ? 'eksi' : 'artı'}` });
    b.addEventListener('click', () => { if (!J.hold) onStep(i, sign); });
    b.addEventListener('pointerdown', (ev) => { if (J.hold && ev.button === 0) { b.setPointerCapture(ev.pointerId); startHold(i, sign); } });
    for (const t of ['pointerup', 'pointercancel', 'lostpointercapture']) b.addEventListener(t, () => stopHold());
    b.addEventListener('contextmenu', (ev) => ev.preventDefault());
    return b;
  }

  /* ---- artımlı ---- */
  async function onStep(axis, sign) {
    if (J.busy) return;
    J.busy = true; apply();
    try {
      await post({ increment: { axis, amount: sign * J.step }, ttl_ms: TTL_MS });
      say('');
    } catch (e) { say(explain(e), true); } finally { J.busy = false; apply(); }
  }

  /* ---- basılı-tut: her 100 ms bir komut (öncekisi bitmediyse atlanır: kuyruk birikmez) ---- */
  function startHold(axis, sign) {
    stopHold(true);
    const twist = [0, 0, 0, 0, 0, 0]; twist[axis] = sign * (axis < 3 ? HOLD_LIN : HOLD_ANG);
    J.held = { twist, inflight: false };
    const beat = async () => {
      const h = J.held; if (!h || h.inflight) return;
      h.inflight = true;
      try { await post({ twist, ttl_ms: TTL_MS }); say(''); }
      catch (e) {
        say(explain(e), true);
        if (e.status === 403) blockHold(e.body && (e.body.message || e.body.error) || 'sunucu reddetti');
        stopHold(true);
      } finally { if (h) h.inflight = false; }
    };
    beat(); J.beat = setInterval(beat, BEAT_MS);
  }
  function stopHold(quiet) {
    if (J.beat) { clearInterval(J.beat); J.beat = null; }
    if (J.held) {
      J.held = null;
      if (!quiet) release(false);              // bırakma = ttl_ms:0; kaybolursa sunucu TTL'i zaten durdurur
    }
  }
  function blockHold(reason) { J.holdBlocked = reason; J.hold = false; apply(); }

  async function release(loud) {
    try { await api('/api/jog', 'POST', { lease: J.lease, ttl_ms: 0 }); if (loud) say('Jog bırakıldı.'); }
    catch (e) { if (loud || e.status !== 423) say(explain(e), true); }
  }

  async function onReady() {
    // 'ready' bir senaryo adımıdır; tek adımlık senaryo yoksa Hub'a doğrudan iş vermeyiz.
    const id = Object.keys((S.ui && S.ui.scenarios) || {}).find((k) => k === 'ready');
    if (!id) { say('“ready” senaryosu yapılandırmada yok (scenarios.yaml).', true); return; }
    try { await api(`/api/scenarios/${id}/run`, 'POST'); say('Hazır pozuna gidiliyor…'); }
    catch (e) { say(explain(e), true); }
  }

  /* ---- çerçeveler ---- */
  async function refreshFrames() {
    try {
      const d = await api('/api/frames');
      J.frames = ['World', 'Tool', ...d.frames.map((f) => f.name)];
    } catch (e) { J.frames = ['World', 'Tool']; }
    const sel = q('jg-frame'); sel.replaceChildren();
    for (const n of J.frames) sel.append(el('option', { value: n, text: n }));
    if (!J.frames.includes(J.frame)) J.frame = 'World';
    sel.value = J.frame;
  }

  /* ---- görünüm ---- */
  function disabledReason(s) {
    if (!s.me.can_control) return 'Arayüz salt okunur: jog kapalı.';
    if (s.estop.latched) return 'ACİL DUR kilidinde (423): jog kapalı. Önce kilidi aç.';
    if (s.runner && s.runner.running) return 'Bir senaryo çalışıyor: jog ve senaryolar birbirini dışlar (409).';
    return null;
  }
  function apply() {
    if (!J.mounted) return;
    const s = J.snap, why = s ? disabledReason(s) : null;
    const lock = !!why || (J.busy && !J.hold);
    for (const b of document.querySelectorAll('#jg-grid .jg-b')) b.disabled = !!why || (J.busy && !J.hold);
    for (const b of document.querySelectorAll('.jg-step')) b.classList.toggle('on', Number(b.dataset.step) === J.step);
    q('jg-ready').disabled = !!why; q('jg-release').disabled = !!(s && !s.me.can_control);
    const allowed = !!(J.st && J.st.hold_allowed) && !J.holdBlocked;
    const hold = q('jg-hold'); hold.disabled = !allowed || !!why; hold.checked = J.hold && allowed;
    q('jg-why').textContent = why || (allowed ? '' : `Basılı-tut kapalı: ${J.holdBlocked || (J.st && J.st.hold_reason) || 'bilinmiyor'}`);
    q('jg-grid').classList.toggle('lock', lock);
    q('jg-grid').dataset.mode = J.hold ? 'hold' : 'increment';
  }
  function onState(s) {
    J.snap = s;
    if (!J.mounted) return;
    if (window.FrameWizard) FrameWizard.onState(s);
    const r = s.robot || {}, tcp = r.tcp;
    if (tcp) {
      [0, 1, 2].forEach((i) => { q(`jg-v${i}`).textContent = (tcp.position[i] * 1000).toFixed(1); });
      [0, 1, 2].forEach((i) => { q(`jg-v${i + 3}`).textContent = tcp.wpr_deg[i].toFixed(1); });
    }
    const jg = r.jog;
    q('jg-info').textContent = jg ? `Jog: ${jg.mode || '—'} · çerçeve ${jg.frame || '—'} · durum ${jg.status}` +
      (jg.messages && jg.messages.length ? ` · ${jg.messages.join('; ')}` : '') : (r.state ? `Robot: ${r.state}` : '');
    apply();
  }
  /* Öğretme sihirbazı (frames.js) için: çerçeve seçimi/yenileme, bırakma, hata metni, ham TCP. */
  function setFrame(name) { J.frame = name; const sel = q('jg-frame'); if (sel) sel.value = name; }
  const tcp = () => (J.snap && J.snap.robot && J.snap.robot.tcp) || null;
  return { mount, onState, explain, release, refreshFrames, setFrame, tcp, frame: () => J.frame };
})();
