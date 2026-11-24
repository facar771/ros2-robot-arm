/* Tezgâh paneli (KM3): tools/bench mantığını Hub'dan sürer. GERÇEK ROBOT DEĞİL: yükten ayrık motor, operatör başında.
 * Kapıların hepsi sunucuda (Bench + firmware) uygulanır; bu dosya yalnızca düğmeleri çizer ve mesajları gösterir.
 * Sayfa görünürken 0,5 sn'de bir GET /api/bench yapar (canlılık işareti); sekme gizlenirse sorgu durur ve Hub
 * ~5 sn sonra stop + DISABLE gönderir. Veri textContent ile yazılır (innerHTML yok). */
'use strict';

window.BenchLogic = (() => {
  /** Sıfırlama onay kutusu işaretli değilse hiçbir şey gönderilmez. */
  const canZero = (confirmed, st) => !!(confirmed && st && st.open && st.bench_mode);
  /** Eksenin enerjili ve sıfırlı olduğu, hareket etmediği durumda hareket düğmeleri açılır. */
  const canMove = (ax, st) => !!(st && st.open && st.bench_mode && st.state === 'ENABLED' && ax && ax.enabled && !ax.moving);
  /** Hedef kutusu (mutlak adım) -> bench'e giden GÖRELİ adım dizgesi; geçersizse null. */
  function deltaFor(targetText, ax) {
    const t = String(targetText).trim();
    if (!/^[+-]?\d{1,5}$/.test(t)) return null;
    const target = parseInt(t, 10);
    if (target < ax.lo || target > ax.hi) return null;       // firmware sınırı: Hub'da da gösterilir, gönderilmez
    const d = target - ax.pos;
    return d === 0 ? null : String(d);
  }
  const stepAmount = (dir, step) => `${dir < 0 ? '-' : '+'}${step}`;
  return { canZero, canMove, deltaFor, stepAmount };
})();

window.BenchPanel = (() => {
  const B = { mounted: false, st: null, timer: null, step: 5, busy: false };
  const q = (id) => document.getElementById(id);
  const L = window.BenchLogic;

  function mount(host) {
    if (B.mounted || !(S.snap && S.snap.bench && S.snap.bench.enabled)) return;
    B.mounted = true;
    const confirm = el('input', { type: 'checkbox', id: 'bn-confirm' });
    confirm.addEventListener('change', render);
    (host || q('panels')).append(el('div', { class: 'card bn', id: 'p-bench' }, [
      el('div', { class: 'bn-band', role: 'alert',
        text: 'TEZGÂH — gerçek robot DEĞİL. Yalnız yükten ayrık motor, operatör başında, güç kesmeye hazır.' }),
      el('h2', { text: 'Tezgâh (3 step motor)' }),
      el('div', { class: 'say', id: 'bn-state' }),
      el('div', { class: 'row' }, [
        el('button', { class: 'small primary', type: 'button', id: 'bn-open', text: 'Tezgâhı aç (COM)' }),
        el('button', { class: 'small', type: 'button', id: 'bn-close', text: 'Kapat (stop + DISABLE)' })]),
      el('div', { id: 'bn-axes', class: 'bn-axes' }),
      el('label', { class: 'jg-hold' }, [confirm, ' motor yükten ayrık, güç kesmeye hazırım (sıfırlamak için işaretle)']),
      el('div', { class: 'row' }, [
        el('button', { class: 'small', type: 'button', id: 'bn-enable', text: 'Enerjile (enable)' }),
        el('button', { class: 'small', type: 'button', id: 'bn-disable', text: 'Enerjiyi kes (disable)' }),
        el('button', { class: 'small', type: 'button', id: 'bn-stop', text: 'STOP' }),
        el('button', { class: 'small', type: 'button', id: 'bn-clear', text: 'Kilidi aç (clear)' })]),
      el('button', { class: 'bn-estop', type: 'button', id: 'bn-estop', text: 'ACİL DUR (TEZGÂH)' }),
      el('ul', { class: 'log', id: 'bn-msg' })]));
    q('bn-open').addEventListener('click', () => act('/api/bench/open'));
    q('bn-close').addEventListener('click', () => act('/api/bench/close'));
    q('bn-enable').addEventListener('click', () => cmd({ cmd: 'enable' }));
    q('bn-disable').addEventListener('click', () => cmd({ cmd: 'disable' }));
    q('bn-stop').addEventListener('click', () => cmd({ cmd: 'stop' }));
    q('bn-clear').addEventListener('click', () => cmd({ cmd: 'clear' }));
    q('bn-estop').addEventListener('click', estop);
    document.addEventListener('visibilitychange', poll);
    poll();
    B.timer = setInterval(poll, 500);
  }

  async function poll() {
    if (typeof document.hidden !== 'undefined' && document.hidden) return;     // gizli sekme: canlılık işareti yok
    try { B.st = await api('/api/bench'); } catch (e) { B.st = null; }
    render();
  }

  async function act(path) {
    if (B.busy) return;
    B.busy = true;
    try { B.st = await api(path, 'POST'); } catch (e) { toast(e.message, true); say([e.message], true); }
    B.busy = false;
    render();
  }

  async function cmd(body) {
    try {
      const res = await api('/api/bench/command', 'POST', body);
      B.st = res; say(res.messages, !res.ok);
    } catch (e) { toast(e.message, true); say([e.message], true); }
    render();
  }

  async function estop() {                              // oturumsuz ve kilitsiz uç; yanıtı beklemeden poll devam eder
    try { await api('/api/bench/estop', 'POST'); toast('Tezgâh ACİL DUR gönderildi'); }
    catch (e) { toast(e.message, true); }
    poll();
  }

  function say(lines, bad) {
    const ul = q('bn-msg'); if (!ul) return;
    ul.replaceChildren(...(lines || []).map((t) => el('li', { class: bad ? 'bad' : '', text: t })));
  }

  function axisRow(ax) {
    const confirmed = !!(q('bn-confirm') && q('bn-confirm').checked);
    const go = (amount) => cmd({ cmd: 'move', axis: ax.name, amount });
    const btn = (label, amount) => {
      const b = el('button', { class: 'jg-b', type: 'button', text: label });
      b.disabled = !L.canMove(ax, B.st);
      b.addEventListener('click', () => go(amount));
      return b;
    };
    const target = el('input', { type: 'text', inputMode: 'numeric', size: 5, placeholder: `${ax.lo}…${ax.hi}`,
      'aria-label': `${ax.name} hedef (adım)` });
    const goBtn = el('button', { class: 'small', type: 'button', text: 'Git' });
    goBtn.disabled = !L.canMove(ax, B.st);
    goBtn.addEventListener('click', () => {
      const d = L.deltaFor(target.value, ax);
      if (d === null) { say([`${ax.name.toUpperCase()}: hedef ${ax.lo}…${ax.hi} arasında, farklı bir tam sayı olmalı`], true); return; }
      go(d);
    });
    const zero = el('button', { class: 'small', type: 'button', text: 'Sıfırla' });
    zero.disabled = !L.canZero(confirmed, B.st) || ax.moving;
    zero.addEventListener('click', () => cmd({ cmd: 'zero', axis: ax.name, confirm: true }));
    const step = B.step;
    return el('div', { class: 'bn-ax' }, [
      el('b', { text: ax.name.toUpperCase() }),
      el('output', { class: 'jg-val', text: `${ax.pos} adım` }),
      el('span', { class: 'rv-tag est', text: 'KESTİRİM' }),
      el('span', { class: 'muted', text: `[${ax.homed ? 'sıfırlı' : 'sıfırsız'}${ax.enabled ? ', enerjili' : ''}${ax.moving ? ', hareketli' : ''}] sınır ${ax.lo}…${ax.hi}` }),
      btn(`− ${step}`, L.stepAmount(-1, step)), btn(`+ ${step}`, L.stepAmount(1, step)), target, goBtn, zero]);
  }

  function render() {
    if (!B.mounted) return;
    const st = B.st;
    const info = !st ? 'Hub’a ulaşılamıyor'
      : !st.open ? (st.bridge_active ? 'ESP32 köprüsü açık: COM tek sahiplidir, Tezgâh açılmaz.' : `kapalı (port ${st.serial || '—'})`)
      : !st.link_ok ? 'hat yok / STATUS gelmiyor'
      : `${st.state}${st.reason && st.reason !== 'NONE' ? ` (${st.reason})` : ''} · ${st.flags.join(', ') || '—'}`
        + (st.bench_mode ? '' : ' · BENCH_MODE YOK: hareket komutu gönderilmez');
    q('bn-state').textContent = info;
    q('bn-open').disabled = !st || st.open || !st.enabled;
    for (const id of ['bn-close', 'bn-enable', 'bn-disable', 'bn-stop', 'bn-clear']) q(id).disabled = !st || !st.open;
    q('bn-enable').disabled = q('bn-enable').disabled || !st.bench_mode;
    q('bn-axes').replaceChildren(...((st && st.open && st.axes) ? st.axes.map(axisRow) : []));
    if (st && st.open) say((st.messages || []).slice(-6), false);
  }

  return { mount, render, _state: B };
})();

/* Pot paneli (P4): firmware pot telemetrisini (ham 10 bit ADC, ~15 Hz) gösterir. Hub'da pot uç noktası henüz YOK
 * (PR'da İSTEK: Robot_3 / Robot_10); veri varsa `S.snap.pot` ya da `GET /api/pot` gövdesinden okunur (biçim:
 * pot_logic.js başı). 404 gelirse panel "uç nokta yok" yazar ve sorguyu keser; sayfa/Tezgâh etkilenmez.
 * Açı, Parametreler sayfasındaki TASLAK kalibrasyondan (pot.<eksen>.*) hesaplanır; kalibrasyon yoksa açı "—". */
window.PotPanel = (() => {
  const P = { mounted: false, timer: null, rows: [], hz: null, missing: false, reason: '', cal: {}, names: {}, host: null };
  const Z = () => window.PotLogic;
  const q = (id) => document.getElementById(id);
  const POLL_MS = 200;

  /** Taslak düzenlemelerinden kanal -> {ad, kalibrasyon}. Anahtar: pot.<eksen>.<alan>. */
  function calibrations(edits) {
    const byJoint = {};
    for (const [k, v] of Object.entries(edits || {})) {
      const m = /^pot\.([a-z0-9_]+)\.(channel|raw_min|raw_max|angle_at_min_deg|angle_at_max_deg)$/.exec(k);
      if (m && typeof v === 'number') (byJoint[m[1]] = byJoint[m[1]] || {})[m[2]] = v;
    }
    const out = {};
    for (const [name, c] of Object.entries(byJoint)) if (Number.isInteger(c.channel)) out[c.channel] = { name, ...c };
    return out;
  }

  async function loadCalibration() {
    try { P.cal = calibrations(((await api('/api/params')).draft || {}).edits); } catch (e) { P.cal = {}; }
  }

  function mount(host) {
    if (P.mounted || !Z()) return;
    P.mounted = true; P.host = host || q('panels');
    P.host.append(el('div', { class: 'card pt', id: 'p-pot' }, [
      el('h2', { text: 'Pot (ham ADC, ~15 Hz)' }),
      el('div', { class: 'say muted', id: 'pt-state', text: 'Veri bekleniyor…' }),
      el('div', { class: 'pt-wrap' }, [el('table', { class: 'pt-tbl', id: 'pt-tbl' })]),
      el('div', { class: 'muted pt-note',
        text: `Ham ${Z().INVALID_LOW} ve altı / ${Z().INVALID_HIGH} ve üstü GEÇERSİZ (kablo/uç); ${Z().STALE_MS} ms'den eski örnek bayat. Açı için Parametreler → Pot kalibrasyonu.` })]));
    loadCalibration();
    render();
  }

  /** Sunucu gövdesini (ya da sahte veriyi) işler ve çizer. */
  function feed(body) {
    const n = Z().normalize(body);
    P.rows = n.rows; P.hz = n.hz; P.reason = n.ok ? '' : (n.reason || 'pot verisi geçersiz');
    render();
  }

  async function poll() {
    if (typeof document.hidden !== 'undefined' && document.hidden) return;
    if (S.snap && S.snap.pot) { feed(S.snap.pot); return; }
    if (P.missing) return;
    try { feed(await api('/api/pot')); }
    catch (e) {
      if (e.status === 404) { P.missing = true; P.rows = []; P.reason = 'Hub’da pot uç noktası yok (GET /api/pot): firmware verisi henüz Hub’a bağlı değil'; stop(); }
      else { P.rows = []; P.reason = e.message || 'pot verisi alınamadı'; }
      render();
    }
  }

  function start() {
    if (!P.mounted || P.timer) return;
    if (P.missing && !(S.snap && S.snap.pot)) { render(); return; }      // uç nokta yok: boşuna sorgu açma
    loadCalibration(); poll(); P.timer = setInterval(poll, POLL_MS);
  }
  function stop() { if (P.timer) { clearInterval(P.timer); P.timer = null; } }

  function row(r) {
    const st = Z().status(r), c = P.cal[r.ch];
    const ang = c && r.raw !== null ? Z().angle(r.raw, c) : null;
    return el('tr', { class: `pt-${st.level}` }, [
      el('td', { text: `K${r.ch}${c ? ` · ${c.name}` : ''}` }),
      el('td', { class: 'pt-num', text: r.raw === null ? '—' : String(r.raw) }),
      el('td', { class: 'pt-num', text: ang === null ? '—' : `${ang.toFixed(1)}°` }),
      el('td', { class: 'pt-num', text: r.ageMs === null ? '—' : `${Math.round(r.ageMs)} ms` }),
      el('td', { text: st.text })]);
  }

  function render() {
    if (!P.mounted) return;
    const t = q('pt-tbl');
    t.replaceChildren(el('thead', {}, [el('tr', {}, ['Kanal', 'Ham', 'Açı', 'Tazelik', 'Durum'].map((h) => el('th', { text: h })))]),
      el('tbody', {}, P.rows.map(row)));
    q('pt-state').textContent = P.reason || (P.rows.length
      ? `${P.rows.length} kanal${P.hz ? ` · ${P.hz} Hz` : ''}${P.rows.some((r) => r.zone !== 'ok') ? ' · GEÇERSİZ ham değer var' : ''}`
      : 'Kanal yok: firmware henüz pot örneği göndermedi');
  }

  return { mount, start, stop, feed, render, calibrations, _state: P };
})();
