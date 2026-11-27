/* Hassas hız kartı (P1d) + sınırsız SİM hızı/ivmesi (P1e). pages/jog.js 400 satır sınırına dayandığı için ayrı dosya (K12).
 * Hesap SUNUCUDA (GET /api/speed/limits); bu dosya yalnız çizer, doğrular ve komut gönderir. Tek hız kaynağı JogPanel'dir
 * (J.speed: jog/Git/klavye hepsi onu okur; kaydırıcı 1–100'ü gösterir, sayı kutusu 400 kabul eder, gerçek değer yanında yazar).
 * Profil limitinin ÜSTÜNE çıkılınca: tabloda sarı satır, üst çubukta "SİM: hız profil sınırının ÜSTÜNDE (varsayımsal)" rozeti
 * (sunucu anlık görüntüsü `robot.speed_over_profile`; pages/jog.js çizer, her sayfada görünür), geçişte Günlük olayı (sunucu yazar). */
'use strict';

(() => {
  const L = new Proxy({}, { get: (_, k) => window.JogLogic[k] });
  const q = (id) => document.getElementById(id);
  const MODE_NOTE = 'Değişiklik bir SONRAKİ komutta geçerli; çalışan iş değişmez. Hub yeniden başlayınca varsayılana döner.';

  function create(P) {
    const SP = { doc: null, seq: 0, timer: null, mode: null, over: false };
    const smsg = (text, bad) => HubPages.say(q('jp-sc-msg'), text, bad);
    const locked = () => !!(SP.doc && SP.doc.scale_locked);

    function show() {
      const v = JogPanel.speed(), box = q('jp-sp-num');
      q('jp-sp-range').value = String(Math.min(L.SPEED_MAX, v));                              // kaydırıcı uca yaslanır
      if (document.activeElement !== box) box.value = String(v);
      q('jp-sp-real').textContent = v > L.SPEED_MAX ? `gerçek değer %${v}` : '';
    }
    function setSpeed(v) {
      const w = L.clampPct(v);
      JogPanel.setSpeed(locked() ? Math.min(w, L.LOCKED_PCT_MAX) : w);
      show();
      clearTimeout(SP.timer); SP.timer = setTimeout(refresh, 200);
    }
    function onBox() {
      const c = L.pctCheck(q('jp-sp-num').value, locked());
      if (!c.ok) { smsg(c.text, true); show(); return; }
      smsg('', false); setSpeed(c.value);
    }
    async function refresh() {
      const my = ++SP.seq;
      try { const d = await api(L.speedLimitsUrl(JogPanel.speed())); if (my === SP.seq) { SP.doc = d; render(); syncServer(d); } }
      catch (e) { if (my === SP.seq) { SP.doc = null; render(); smsg(L.explain(e), true); } }
    }
    /* Kart yüzdesi profil sınırını geçince/dönünce sunucuya bildirilir: robot hızı + rozet + Günlük olayı ancak böyle güncellenir. */
    function syncServer(d) {
      const over = d.above_profile === true;
      if (over === SP.over) return;
      SP.over = over;
      if (!d.scale_locked && !L.disabledReason(P.snap)) api('/api/sim/speed', 'POST', { speed_pct: JogPanel.speed() }).catch(() => null);
    }
    function render() {
      const v = L.speedView(SP.doc), host = q('jp-sp-table'), th = (t) => el('th', { text: t });
      host.replaceChildren(el('thead', {}, [el('tr', {}, [th('Eklem'), th('Limit rad/s'), th('Limit °/s'), th('Etkin rad/s'), th('Etkin °/s'), th('Etkin ivme rad/s²')])]),
        el('tbody', {}, v.rows.map((r) => el('tr', { class: `${r.limiting ? 'lim' : ''} ${r.over ? 'over' : ''}`.trim(), 'data-over': r.over ? '1' : '0' },
          [el('th', { text: r.over ? `▲ ${r.name}` : r.name }), ...[...r.limit, ...r.eff, r.acc].map((t) => el('td', { text: t }))]))));
      q('jp-sp-note').textContent = v.note ? `${v.note}${v.example ? ` · ${v.example}` : ''}` : '';
      const sc = q('jp-sc-num'), ac = q('jp-ac-num'), lk = q('jp-ac-link');
      if (v.scale !== null && document.activeElement !== sc) sc.value = String(v.scale);
      if (v.accelScale !== null && document.activeElement !== ac) ac.value = String(v.accelScale);
      if (document.activeElement !== lk && SP.doc) lk.checked = v.accelLinked;
      q('jp-sp-over').hidden = !v.over;
      q('jp-sp-over').textContent = v.over ? `▲ Profil sınırının ÜSTÜNDE (${v.overWhat}): varsayımsal, yalnız SİM.` : '';
      q('jp-sc-why').textContent = v.locked ? `Kilitli: ${v.reason}` : MODE_NOTE;
      apply();
    }
    function apply() {
      const v = L.speedView(SP.doc), off = v.locked || !SP.doc || !!L.disabledReason(P.snap);
      for (const id of ['jp-sc-num', 'jp-sc-apply', 'jp-ac-link']) { const n = q(id); if (n) n.disabled = off; }
      const lk = q('jp-ac-link'), ac = q('jp-ac-num');
      if (ac && lk) ac.disabled = off || lk.checked;                                          // bağlıyken hız ölçeğiyle birlikte hareket eder
      const box = q('jp-sp-num'); if (box) box.max = String(v.locked ? L.LOCKED_PCT_MAX : L.PCT_MAX);
      if (v.locked && JogPanel.speed() > L.LOCKED_PCT_MAX) setSpeed(L.LOCKED_PCT_MAX);        // gerçek/tezgâhta profil sınırı aşılamaz
    }
    async function onScale() {
      const c = L.scaleCheck(q('jp-sc-num').value, 'Hız ölçeği'), linked = q('jp-ac-link').checked;
      if (!c.ok) return smsg(c.text, true);
      const body = { scale: c.value, speed_pct: JogPanel.speed(), accel_linked: linked };
      if (!linked) {
        const a = L.scaleCheck(q('jp-ac-num').value, 'İvme ölçeği');
        if (!a.ok) return smsg(a.text, true);
        body.accel_scale = a.value;
      }
      try {
        const d = await api('/api/sim/speed', 'POST', body);
        smsg(`Hız ölçeği ${c.value}, ivme ölçeği ${d.accel_scale}${d.accel_linked ? ' (bağlı)' : ''}: bir sonraki komutta geçerli.`);
        await refresh();
      } catch (e) { smsg(L.explain(e), true); }
    }
    function onState(s) {
      if (!q('p-speed')) return;
      const m = (s.robot || {}).mode;
      if (SP.mode !== m) { SP.mode = m; refresh(); } else apply();
    }
    function build() {
      const nb = (d) => { const b = el('button', { class: 'small jp-nb', type: 'button', 'data-delta': String(d), text: `${d > 0 ? '+' : '−'}${Math.abs(d)}`,
        'aria-label': `Hız ${d > 0 ? 'artır' : 'azalt'} ${Math.abs(d)}` }); b.addEventListener('click', () => setSpeed(L.speedNudge(JogPanel.speed(), d))); return b; };
      const rng = el('input', { type: 'range', id: 'jp-sp-range', min: String(L.SPEED_MIN), max: String(L.SPEED_MAX), step: '0.1', 'aria-label': 'Hız yüzdesi' });
      rng.addEventListener('input', () => setSpeed(rng.value));
      const box = el('input', { type: 'number', id: 'jp-sp-num', min: String(L.PCT_MIN), max: String(L.PCT_MAX), step: 'any', inputMode: 'decimal', 'aria-label': 'Hız yüzdesi (sayı)' });
      box.addEventListener('change', onBox);
      const scl = (id, label) => el('input', { type: 'number', id, min: '0', max: '1000', step: 'any', inputMode: 'decimal', 'aria-label': label });
      const link = el('input', { type: 'checkbox', id: 'jp-ac-link', checked: true, 'aria-label': 'İvme ölçeği hız ölçeğiyle birlikte hareket etsin' });
      link.addEventListener('change', apply);
      const ap = el('button', { class: 'small', id: 'jp-sc-apply', type: 'button', text: 'Uygula' }); ap.addEventListener('click', onScale);
      return el('div', { class: 'card', id: 'p-speed' }, [el('h2', { text: 'Hız (hassas)' }),
        el('div', { class: 'jp-sp' }, [nb(-5), nb(-1), rng, nb(1), nb(5), el('label', { class: 'jp-f' }, [box, el('span', { text: '%' })]),
          el('span', { class: 'say', id: 'jp-sp-real' })]),
        el('div', { class: 'say jp-over', id: 'jp-sp-over', role: 'status', hidden: true }),
        el('div', { class: 'jp-tablewrap' }, [el('table', { class: 'jp-sp-table', id: 'jp-sp-table' })]),
        el('div', { class: 'say', id: 'jp-sp-note' }),
        el('div', { class: 'row' }, [el('label', { class: 'jp-f' }, [el('span', { text: 'Hız tavanı ölçeği' }), scl('jp-sc-num', 'Hız tavanı ölçeği')]),
          el('label', { class: 'jp-f' }, [el('span', { text: 'İvme tavanı ölçeği' }), scl('jp-ac-num', 'İvme tavanı ölçeği')]),
          el('label', { class: 'jp-f' }, [link, el('span', { text: 'hız ölçeğiyle birlikte' })]), ap]),
        el('div', { class: 'say', id: 'jp-sc-why' }), el('div', { class: 'say jp-msg', id: 'jp-sc-msg', role: 'alert' })]);
    }
    return { build, show, setSpeed, refresh, apply, onState };
  }

  window.SpeedCard = { create };
})();
