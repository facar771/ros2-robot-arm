/* Düzlem (User frame) öğretme sihirbazı (L3; jog.js'ten sonra, jog kartı çizilince bağlanır).
 * Üç nokta: orijin, +X yönü, XY düzlemi. Her nokta robot JOG ile götürülüp "şu anki TCP'yi kaydet"le alınır
 * (sihirbaz robotu HAREKET ETTİRMEZ). Kaydet = tüm liste PUT /api/frames; dosyayı ROBOT yazar, Hub yazmaz.
 * Geçerlilik (20 mm, aynı doğru, ad, en çok çerçeve) robotta denetlenir: onun hata metni olduğu gibi gösterilir.
 * Veri textContent ile yazılır (innerHTML yok). */
'use strict';

window.FrameWizard = (() => {
  const STEPS = [['origin', 'Orijin (O)', 'Robotu düzlemin orijin noktasına jog ile götür.'],
                 ['x', '+X yönü', 'Orijinden +X doğrultusunda en az 20 mm uzaktaki bir noktaya götür.'],
                 ['xy', 'XY düzlemi (+Y tarafı)', 'X ekseninin dışında, düzlemin +Y yarısında bir noktaya götür.']];
  const W = { mounted: false, pts: {}, frames: [], max: 0, busy: false, confirmDel: null, snap: null };
  const q = (id) => document.getElementById(id);
  const mm = (p) => p.map((v) => (v * 1000).toFixed(1)).join(', ');

  function mount() {
    if (W.mounted) return;
    W.mounted = true;
    const steps = el('div', { class: 'fw-steps', id: 'fw-steps' });
    for (const [key, title, hint] of STEPS) {
      const b = el('button', { class: 'small', type: 'button', id: `fw-rec-${key}`, text: 'Şu anki TCP’yi kaydet' });
      b.addEventListener('click', () => record(key));
      steps.append(el('div', { class: 'fw-step', id: `fw-s-${key}` }, [
        el('b', { text: title }), el('span', { class: 'muted', text: hint }),
        el('output', { class: 'fw-pt', id: `fw-v-${key}`, text: 'kaydedilmedi' }), b]));
    }
    q('panels').append(el('div', { class: 'card', id: 'p-frames' }, [
      el('h2', { text: 'Düzlemler (3 nokta öğretme)' }),
      el('ul', { class: 'fw-list', id: 'fw-list' }),
      el('details', { id: 'fw-new' }, [
        el('summary', { text: 'Yeni düzlem öğret' }),
        el('div', { class: 'fw-form' }, [
          el('label', { class: 'jg-f', text: 'Ad ' }, [el('input', { type: 'text', id: 'fw-name', maxLength: 24,
            placeholder: 'örn. Tezgah', autocomplete: 'off', 'aria-label': 'Düzlem adı' })]),
          steps,
          el('div', { class: 'row' }, [
            el('button', { class: 'small primary', type: 'button', id: 'fw-save', text: 'Düzlemi kaydet' }),
            el('button', { class: 'small', type: 'button', id: 'fw-clear', text: 'Noktaları sil' })])])]),
      el('div', { class: 'say jg-msg', id: 'fw-msg', role: 'alert' })]));
    q('fw-save').addEventListener('click', save);
    q('fw-clear').addEventListener('click', () => { W.pts = {}; render(); say(''); });
    load();
  }

  function say(text, bad = false) {
    const n = q('fw-msg'); if (!n) return;
    n.textContent = text || ''; n.classList.toggle('bad', !!bad);
  }

  async function load() {
    try {
      const d = await api('/api/frames');
      W.frames = d.frames; W.max = d.max_frames;
    } catch (e) { say(JogPanel.explain(e), true); }
    render();
  }

  function record(key) {
    const t = JogPanel.tcp();
    if (!t || !t.position) { say('TCP konumu okunamıyor: kayıt yapılamadı (eklem durumu yok).', true); return; }
    if (t.warning) say(`Uyarı: ${t.warning}`);
    else say('');
    W.pts[key] = t.position.slice();          // metre, robotun verdiği gibi
    render();
  }

  async function putAll(list) {
    await JogPanel.release(false);            // jog etkinken robot 409 verir; önce bırak
    return api('/api/frames', 'PUT', { frames: list });
  }
  const asBody = (f) => ({ name: f.name, points: f.points });

  async function save() {
    if (W.busy) return;
    const name = q('fw-name').value.trim();
    if (!name) { say('Önce düzleme bir ad ver.', true); return; }
    const missing = STEPS.filter(([k]) => !W.pts[k]).map(([, t]) => t);
    if (missing.length) { say(`Eksik nokta: ${missing.join(', ')}`, true); return; }
    W.busy = true; render();
    try {
      const cur = (await api('/api/frames')).frames;        // taze liste: başka istemci değiştirmiş olabilir
      if (cur.some((f) => f.name === name)) { say(`“${name}” adı zaten var: başka ad seç ya da önce sil.`, true); return; }
      const d = await putAll([...cur.map(asBody), { name, points: { origin: W.pts.origin, x: W.pts.x, xy: W.pts.xy } }]);
      W.frames = d.frames; W.pts = {}; q('fw-name').value = '';
      await JogPanel.refreshFrames();
      const f = d.frames.find((x) => x.name === name);
      say(`“${name}” kaydedildi` + (f && f.warnings && f.warnings.length ? ` — uyarı: ${f.warnings.join('; ')}` : '.'));
    } catch (e) { say(JogPanel.explain(e), true); } finally { W.busy = false; render(); }
  }

  async function remove(name) {
    if (W.confirmDel !== name) { W.confirmDel = name; render(); return; }
    W.confirmDel = null; W.busy = true; render();
    try {
      const cur = (await api('/api/frames')).frames;
      const d = await putAll(cur.filter((f) => f.name !== name).map(asBody));
      W.frames = d.frames;
      if (JogPanel.frame() === name) JogPanel.setFrame('World');
      await JogPanel.refreshFrames();
      say(`“${name}” silindi.`);
    } catch (e) { say(JogPanel.explain(e), true); } finally { W.busy = false; render(); }
  }

  function useFrame(name) { JogPanel.setFrame(name); say(`Jog çerçevesi: ${name}`); }

  function render() {
    const s = W.snap, ro = !!(s && (!s.me.can_control || s.estop.latched));
    const list = q('fw-list'); if (!list) return;
    list.replaceChildren();
    if (!W.frames.length) list.append(el('li', { class: 'muted', text: 'Kayıtlı düzlem yok. Yerleşik: World, Tool.' }));
    for (const f of W.frames) {
      const sel = el('button', { class: 'small', type: 'button', text: 'Jog’da seç' });
      sel.addEventListener('click', () => useFrame(f.name));
      const del = el('button', { class: 'small', type: 'button', disabled: W.busy || ro,
        text: W.confirmDel === f.name ? 'Emin misin? Sil' : 'Sil' });
      del.addEventListener('click', () => remove(f.name));
      const o = f.points && f.points.origin;
      list.append(el('li', { class: 'fw-item' }, [
        el('b', { text: f.name }),
        el('span', { class: 'muted', text: (o ? ` O(mm): ${mm(o)}` : '') +
          (f.wpr_deg ? ` · W/P/R: ${f.wpr_deg.join(', ')}°` : '') }),
        ...(f.warnings && f.warnings.length ? [el('span', { class: 'fw-warn', text: ` ⚠ ${f.warnings.join('; ')}` })] : []),
        sel, del]));
    }
    for (const [key] of STEPS) {
      const p = W.pts[key];
      q(`fw-v-${key}`).textContent = p ? `${mm(p)} mm` : 'kaydedilmedi';
      q(`fw-s-${key}`).classList.toggle('done', !!p);
      q(`fw-rec-${key}`).disabled = W.busy || !!(s && !s.me.can_control);
    }
    q('fw-save').disabled = W.busy || ro || (W.max > 0 && W.frames.length >= W.max);
    if (W.max && W.frames.length >= W.max) say(`En çok ${W.max} düzlem: yenisi için birini sil.`, true);
  }

  function onState(s) { W.snap = s; if (W.mounted) render(); }
  return { mount, onState };
})();
