/* Sayfa: Algoritmalar (A1). Parametreli üreteçler (ızgara/palet pick-place, daire/yay, zigzag) Program adımı üretir;
 * her adım sunucuda KURU DOĞRULANIR (/api/algo/generate -> /api/move/check anlamı), kutu dışı adım KIRMIZI olur,
 * önizleme 3B'de çizgi + işaret (HubScene), "Programa ekle" adımları Program sayfasına aktarır (HubProgram.addSteps).
 * Robot BU SAYFADA HAREKET ETMEZ. Doğrulama robotun şu anki pozundan başlar ve ÖNERİDİR: asıl kapı Program'ın kendi
 * önizlemesi ve çalışırken her adımdaki arka uç denetimidir. Veri yalnız textContent ile yazılır (HTML dizgesi enjekte edilmez). */
'use strict';

(() => {
  const DEBOUNCE_MS = 350, SPEED_PCT = 25, MAX_LISTED = 6, MAX_ROWS = 60, MAX_MARKS = 80, MAX_BAD = 40, MARK_R_M = 0.008, BAD_R_M = 0.011;
  const COLORS = { path: '#a371f7', pick: '#e3b341', place: '#4f8cff', start: '#3fb950', end: '#4f8cff', line: '#4f8cff', bad: '#f85149' };
  const KIND = { move_l: 'Doğrusal (L)', move_j: 'Eklem (J)', tool: 'Alet', wait: 'Bekle', comment: 'Not' };
  const NUM_RE = /^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/, SC = 'algo:';

  /* ---- saf mantık (DOM yok; node testi doğrudan çağırır) ---- */
  const L = {
    parse(f, text) {                           // alan metni -> {ok, value} | {ok:false, why}; NaN/∞/üstel/onaltılık yazım reddedilir
      if (f.options) return f.options.some((o) => o.value === text) ? { ok: true, value: text } : { ok: false, why: 'listeden seç' };
      const t = String(text).trim();
      if (!NUM_RE.test(t)) return { ok: false, why: 'sayı olmalı' };
      const v = Number(t.replace(',', '.'));    // Türkçe ondalık virgülü: "12,5"
      if (f.integer && !Number.isInteger(v)) return { ok: false, why: 'tamsayı olmalı' };
      return v < f.min || v > f.max ? { ok: false, why: `${f.min} … ${f.max} aralığında olmalı` } : { ok: true, value: v };
    },
    describe(s) {                              // adım -> tek satır Türkçe özet (mm: kullanıcı mm düşünür, model metre tutar)
      if (s.kind === 'move_l') return `x ${(s.pos_m[0] * 1000).toFixed(1)} · y ${(s.pos_m[1] * 1000).toFixed(1)} · z ${(s.pos_m[2] * 1000).toFixed(1)} mm${s.orientation === 'free' ? ' · yönelim serbest' : ''}`;
      if (s.kind === 'tool') return `%${s.open_pct} açık`;
      return s.kind === 'wait' ? `${s.s} sn` : s.text || '';
    },
    rowMap: (res) => new Map(res.check.rows.map((r) => [r.line, r])),
    programName(title) {                       // adsız Program'a varsayılan ad (program_store.NAME_RE: harf/rakam/boşluk/. _ -, en çok 40)
      return String(title).replace(/[^0-9A-Za-zÇĞİÖŞÜçğıöşü _.-]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 40) || 'Algoritma';
    },
    visible(res) {                             // gösterilecek satırlar: ilk MAX_ROWS + TÜM ihlalliler (ihlal asla gizlenmez)
      const bad = res.check.rows.filter((r) => !r.ok).map((r) => r.line), keep = new Set([...res.steps.keys()].slice(0, MAX_ROWS).concat(bad));
      return { idx: [...keep].sort((a, b) => a - b), hidden: res.steps.length - keep.size };
    },
    summary(res) {
      const v = res.check.violations, n = res.check.rows.length;
      if (v === 0) return `${res.count} adım · ✓ ${n} hareket/alet adımı güvenli kutuda ve ulaşılabilir`;
      const bad = res.check.rows.filter((r) => !r.ok).map((r) => r.line + 1);       // liste kaydırmalıdır: ihlalli adım numaraları özette
      return `${res.count} adım · ✗ ${v} ihlal (adım ${bad.slice(0, MAX_LISTED).join(', ')}${bad.length > MAX_LISTED ? ' …' : ''}): kırmızı satırlar`;
    },
    scene(res) {                               // 3B çizim planı: yol, numaralı işaretler, ihlalli noktalar (kırmızı) + ilgili parça
      const badLines = new Set(res.check.rows.filter((r) => !r.ok).map((r) => r.line)), marked = new Set(res.marks.map((m) => m.i));
      const marks = res.marks.slice(0, MAX_MARKS).map((m) => ({ id: `${SC}m${m.i}`, pos: res.steps[m.i].pos_m, number: m.n,
        color: badLines.has(m.i) ? COLORS.bad : COLORS[m.role] || COLORS.place }));
      const bad = [];
      res.points.forEach((p, k) => {
        if (!badLines.has(p.i) || bad.length >= MAX_BAD) return;
        bad.push({ id: `${SC}b${p.i}`, pos: p.pos_m, marker: !marked.has(p.i), seg: k > 0 ? { id: `${SC}s${p.i}`, pts: [res.points[k - 1].pos_m, p.pos_m] } : null });
      });
      return { path: res.points.length > 1 ? res.points.map((p) => p.pos_m) : null, marks, bad };
    },
  };

  /* ---- durum ve G/Ç ---- */
  const Q = { inputs: {} }, P = { kinds: [], kind: null, vals: {}, bad: {}, res: null, backend: null, seq: 0, shown: false, ids: [], control: true, timer: null };
  const E = el, on = (e, ev, fn) => { e.addEventListener(ev, fn); return e; };
  async function call(path, method = 'GET', body) {   // hata fırlatmaz: {failed:true,status,message,body}
    try { return await api(path, method, body); } catch (e) { return { failed: true, status: e.status, message: e.message, body: e.body || {} }; }
  }
  const say = (text, bad = false) => HubPages.say(Q.msg, text, bad);

  /* ---- form: alanlar sunucudaki üreteç tanımından kurulur (tek doğruluk kaynağı: hub_core/algo_gen.py) ---- */
  function edit(f, input) {
    const id = P.kind.id, r = L.parse(f, input.value);
    input.classList.toggle('bad', !r.ok);
    if (r.ok) { P.vals[id][f.key] = r.value; delete P.bad[id][f.key]; } else P.bad[id][f.key] = `${f.label}: ${r.why}`;
    // alandan çıkarken (ör. doğrudan "Programa ekle"ye dokunurken) sonuç eskir: ekleme kapanır, neden hemen yazılır
    say(r.ok ? 'Değer değişti: yeniden denetleniyor…' : `Geçersiz değer — ${P.bad[id][f.key]}`, !r.ok);
    clearTimeout(P.timer); P.res = null; Q.went.hidden = true; paint(); P.timer = setTimeout(generate, DEBOUNCE_MS);
  }
  function buildForm() {
    const k = P.kind; Q.inputs = {}; Q.about.textContent = k.about;
    Q.form.replaceChildren(...k.fields.map((f) => {
      const input = f.options ? E('select', { title: f.label }, f.options.map((o) => E('option', { value: o.value, text: o.label })))
        : E('input', { type: 'text', inputMode: 'decimal', autocomplete: 'off', title: f.label });
      on(input, 'change', () => edit(f, input)); input.value = String(P.vals[k.id][f.key]); Q.inputs[f.key] = input;
      return E('label', { class: 'al-f' }, [E('span', { text: f.unit ? `${f.label} (${f.unit})` : f.label }), input]);
    }));
    Q.kinds.forEach((b, i) => b.setAttribute('aria-pressed', String(P.kinds[i].id === k.id)));
  }
  function pick(id) {
    P.kind = P.kinds.find((k) => k.id === id); P.res = null; clearTimeout(P.timer);
    buildForm(); paint(); generate();
  }

  async function generate() {
    const id = P.kind.id, bad = Object.values(P.bad[id]);
    if (bad.length) { say(`Geçersiz değer — ${bad[0]}`, true); return; }
    const my = ++P.seq; P.res = null; Q.went.hidden = true; say('Üretiliyor ve denetleniyor…'); paint(); syncScene();
    const r = await call('/api/algo/generate', 'POST', { kind: id, params: P.vals[id], speed_pct: SPEED_PCT });
    if (my !== P.seq) return;                  // eski yanıt yenisini ezmesin
    if (r.failed) {
      say(r.message, true); P.res = null;
      for (const e of r.body.errors || []) if (Q.inputs[e.field]) Q.inputs[e.field].classList.add('bad');   // sunucu hangi alanı reddettiyse kırmızı
    } else { P.res = r; P.backend = r.backend; say(''); }
    paint(); syncScene();
  }

  /* ---- sonuç: özet + adım listesi (ihlalli satır kırmızı, neden yazılı) ---- */
  function rowEl(res, i, rows) {
    const s = res.steps[i], row = rows.get(i), bad = row && !row.ok;
    const why = bad ? row.violations.map((v) => v.message).join(' · ') : '';
    return E('div', { class: `al-step ${bad ? 'bad' : row ? 'ok' : 'note'}`, 'data-line': String(i) }, [
      E('span', { class: 'al-n', text: String(i + 1) }), E('b', { text: KIND[s.kind] || s.kind }), E('span', { class: 'al-d', text: L.describe(s) }),
      E('span', { class: 'al-why', text: why })]);
  }
  function paint() {
    if (!Q.sum) return;
    const r = P.res;
    Q.sum.textContent = r ? L.summary(r) : ''; Q.sum.className = `al-sum ${r ? (r.check.ok ? 'ok' : 'bad') : ''}`;
    Q.add.disabled = !r || !P.control;
    Q.note.textContent = r && !r.check.ok ? `İhlalli ${r.check.violations} adım da eklenir; Program'da kırmızı görünür ve Çalıştır kapalı kalır.` : '';
    if (!r) { Q.list.replaceChildren(); Q.more.textContent = ''; } else {
      const rows = L.rowMap(r), v = L.visible(r);
      Q.list.replaceChildren(...v.idx.map((i) => rowEl(r, i, rows))); Q.more.textContent = v.hidden ? `… ${v.hidden} adım daha (ihlalli olanların hepsi yukarıda)` : '';
    }
    Q.mock.hidden = !(P.backend && P.backend.mock); Q.mock.textContent = `${P.backend ? P.backend.label : ''} hareket servisi: ölçüm değildir, gerçek robot hareket etmez.`;
  }

  function addToProgram() {
    const r = P.res, H = window.HubProgram;
    if (!r) return;
    if (!H) { toast('Program sayfası yüklenemedi', true); return; }
    const out = H.addSteps(r.steps, L.programName(P.kind.title));
    if (!out.ok) { toast(out.message, true); return; }
    Q.went.hidden = false; Q.wentTxt.textContent = `${r.count} adım Program'a eklendi (toplam ${out.total}). Önizle ile yeniden denetle.`; toast(`${r.count} adım Program'a eklendi`);
  }

  /* ---- 3B önizleme (HubScene, UI_CONTRACT §9-C): yol çizgisi + numaralı işaret; ihlalli nokta/parça kırmızı. Kimlik öneki `algo:`.
   * İşlevler fırlatmaz, {ok:false} döner: her sonuç denetlenir ve hata ekranda yazılır (sessiz çizmeme yok). ---- */
  const sceneOf = () => { const H = window.HubScene; return H && H.implemented !== false ? H : null; };
  function clearScene() { const H = sceneOf(); if (H) P.ids.forEach((id) => H.removeObject(id)); P.ids = []; }
  function syncScene() {
    const H = sceneOf(); clearScene(); if (!H || !P.shown) return;
    const errs = [], put = (o) => { const r = H.addObject(o); if (r.ok) P.ids.push(o.id); else errs.push(r.message || r.error); };
    const sc = P.res ? L.scene(P.res) : null;
    if (sc) {
      if (sc.path) put({ id: `${SC}path`, kind: 'polyline', layer: 'path', geometry: { points_m: sc.path }, color: COLORS.path });
      for (const m of sc.marks) put({ id: m.id, kind: 'marker', layer: 'markers', pose: { pos_m: m.pos }, geometry: m.number == null ? { radius_m: MARK_R_M } : { radius_m: MARK_R_M, number: m.number }, color: m.color });
      for (const b of sc.bad) {
        if (b.seg) put({ id: b.seg.id, kind: 'polyline', layer: 'path', geometry: { points_m: b.seg.pts }, color: COLORS.bad });
        if (b.marker) put({ id: b.id, kind: 'marker', layer: 'markers', pose: { pos_m: b.pos }, geometry: { radius_m: BAD_R_M }, color: COLORS.bad });
      }
    }
    Q.scene.textContent = errs.length ? `3B sahne: ${errs[0]}` : '';
  }

  /* ---- sayfa iskeleti ---- */
  function build(root) {
    const sec = (title, kids, cls = '') => E('div', { class: `card al-card ${cls}` }, [E('h2', { text: title }), ...kids]);
    Q.mock = E('div', { class: 'banner', role: 'status', hidden: true });
    Q.kinds = P.kinds.map((k) => on(E('button', { type: 'button', class: 'small al-kind', text: k.title }), 'click', () => pick(k.id)));
    Q.about = E('div', { class: 'say' }); Q.form = E('div', { class: 'al-form' }); Q.msg = E('div', { class: 'say', role: 'status' });
    Q.sum = E('div', { class: 'al-sum' }); Q.note = E('div', { class: 'say' }); Q.list = E('div', { class: 'al-list' }); Q.more = E('div', { class: 'say' });
    Q.add = on(E('button', { type: 'button', class: 'primary', text: 'Programa ekle' }), 'click', addToProgram);
    Q.wentTxt = E('span', { class: 'say' });
    Q.went = E('div', { class: 'row', hidden: true }, [Q.wentTxt, E('a', { class: 'small al-link', href: '#/program', text: 'Program sayfasına git' })]);
    Q.view = E('div', { class: 'al-view' }); Q.scene = E('div', { class: 'say', role: 'status' });
    root.append(E('div', { class: 'page-grid two al' }, [E('div', { class: 'al-side' }, [Q.view, Q.scene]),
      E('div', { class: 'al-main' }, [Q.mock, sec('Algoritma', [E('div', { class: 'al-kinds' }, Q.kinds), Q.about, Q.form, Q.msg], 'al-c1'),
        sec('Sonuç', [Q.sum, Q.note, E('div', { class: 'al-btns' }, [Q.add]), Q.went, Q.list, Q.more], 'al-c2')])]));
  }

  HubPages.register({
    id: 'algo', title: 'Algoritmalar', icon: '🧮', order: 35, styles: ['/pages/algo.css'], logic: L,
    async mount(root) {
      const r = await call('/api/algo/kinds');
      if (r.failed) { root.append(E('div', { class: 'card' }, [E('div', { class: 'say bad', role: 'alert', text: `Üreteçler alınamadı: ${r.message}` })])); return; }
      P.kinds = r.kinds; P.shown = true;
      for (const k of P.kinds) { P.vals[k.id] = Object.fromEntries(k.fields.map((f) => [f.key, f.default])); P.bad[k.id] = {}; }
      build(root); pick(P.kinds[0].id);
      await HubPages.mountView(Q.view); syncScene();                          // HubScene tuval kurulunca vardır
    },
    onShow() {                                 // geri dönünce robot başka yerde olabilir: doğrulama yenilenir
      P.shown = true; if (Q.view) HubPages.mountView(Q.view).then(syncScene);
      if (P.res) generate();
    },
    onHide() { P.shown = false; clearTimeout(P.timer); clearScene(); },     // başka sayfa 3B'yi kullanır: işaretlerimiz kalkar
    onState(s) { P.control = !!(s.me && s.me.can_control); paint(); },
  });
})();
