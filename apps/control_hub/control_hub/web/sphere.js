/* Küresel çerçeve sihirbazı (L8, Hub kısmı; frames.js'ten ÖNCE yüklenir).
 *
 * İKİ PARÇA:
 *  1) FrameTypes: DOM'suz, saf mantık (node ile test edilir). Çerçeve türü kuralları (Robot_1 kararı):
 *     - GET /api/frames yanıtında `types` listesinde `sphere` yoksa küre oluşturma ARAYÜZDE YOKTUR.
 *     - Yazarken mevcut diğer türler KORUNUR: kullanıcı çerçeveleri `frames`, küreler `spheres` listesinde
 *       taze GET'ten geri yollanır; hiçbir tür "replace_all" ile silinmez.
 *     - Bilinmeyen `type` SESSİZCE ATLANMAZ: net hata gösterilir ve HİÇBİR ŞEY yazılmaz (atlansaydı sonraki
 *       toplu PUT o çerçeveyi silerdi).
 *  2) SphereWizard: merkez C (mm) + yarıçap r (mm), "şu anki TCP'yi merkez al / 10 cm yukarı", erişim aralığı
 *     (POST /api/frames/reach), kaydet (toplu PUT). Sihirbaz robotu HAREKET ETTİRMEZ. Erişim aralığı
 *     ÇARPIŞMA MODELİ OLMADAN ve kaba ızgarayla hesaplanır; bir güvenlik garantisi değildir.
 * Veri textContent ile yazılır (innerHTML yok). Birim: arayüz mm, API metre. */
'use strict';

(() => {
  const USER = 'user', SPHERE = 'sphere';
  const NAME_RE = /^[A-Za-z][A-Za-z0-9_-]{0,23}$/;
  const AXES = ['z', '-z', 'x', '-x', 'y', '-y'];
  const UP_MM = 100;                                     // "10 cm yukarı"

  /* ---------------------------- saf mantık ---------------------------- */
  const entries = (doc, key) => (doc && Array.isArray(doc[key]) ? doc[key] : []);

  /* Bilinmeyen türdeki girdilerin açıklamaları (boşsa her şey bilinen türde). `type` yoksa varsayılan:
   * frames -> user, spheres -> sphere. */
  function unknownTypes(doc) {
    const bad = [];
    entries(doc, 'frames').forEach((f, i) => {
      const t = f && typeof f === 'object' ? (f.type === undefined ? USER : f.type) : null;
      if (t !== USER) bad.push(`frames[${i}]${f && f.name ? ` “${f.name}”` : ''} türü ${JSON.stringify(t)}`);
    });
    entries(doc, 'spheres').forEach((s, i) => {
      const t = s && typeof s === 'object' ? (s.type === undefined ? SPHERE : s.type) : null;
      if (t !== SPHERE) bad.push(`spheres[${i}]${s && s.name ? ` “${s.name}”` : ''} türü ${JSON.stringify(t)}`);
    });
    return bad;
  }

  function typeError(doc) {
    const bad = unknownTypes(doc);
    if (!bad.length) return null;
    return `Robot, bu Hub’ın tanımadığı çerçeve türleri bildirdi (${bad.join('; ')}). Sessizce atlanmaz: ` +
      'hiçbir şey yazılmadı. Hub’ı güncelle (sayfayı yenile / yeni .exe).';
  }

  /* Küre oluşturma bu robotta var mı? (`types` yoksa ya da sphere içermiyorsa HAYIR: eski robot.) */
  const canSphere = (doc) => !!(doc && Array.isArray(doc.types) && doc.types.includes(SPHERE));

  const userBodies = (doc) => {
    const err = typeError(doc); if (err) throw new Error(err);
    return entries(doc, 'frames').map((f) => ({ name: f.name, points: f.points }));
  };

  /* Toplu PUT gövdesi: mevcut TÜM kullanıcı çerçeveleri + mevcut TÜM küreler (robotun verdiği gibi) + yeni. */
  function withSphere(doc, sphere) {
    return { frames: userBodies(doc), spheres: [...entries(doc, 'spheres'), sphere] };
  }
  function withoutSphere(doc, name) {
    return { frames: userBodies(doc), spheres: entries(doc, 'spheres').filter((s) => s.name !== name) };
  }

  const num = (text) => {
    const t = String(text === undefined || text === null ? '' : text).trim().replace(',', '.');
    if (!t || !/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return NaN;
    return Number(t);
  };
  const toM = (mm) => Number((mm / 1000).toFixed(6));
  const fmtMm = (m) => (m * 1000).toFixed(1);

  /* Girdiyi (mm) API gövdesine (m) çevirir. Aralık denetimi (yarıçap, 5 m) ROBOTTA; onun metni gösterilir. */
  function parseCenterRadius(f) {
    const c = [f.cx, f.cy, f.cz].map(num), r = num(f.r);
    if (c.some((v) => !Number.isFinite(v))) return { error: 'Merkez X, Y, Z sayı olmalı (mm).' };
    if (!Number.isFinite(r)) return { error: 'Yarıçap r sayı olmalı (mm).' };
    const tool_axis = AXES.includes(f.axis) ? f.axis : 'z';
    return { center: c.map(toM), radius: toM(r), tool_axis };
  }

  function parseSphereForm(f, doc) {
    const name = String(f.name || '').trim();
    if (!NAME_RE.test(name)) return { error: 'Ad harfle başlamalı; harf, rakam, _ ve - içerebilir (en çok 24).' };
    const taken = ['World', 'Tool', ...entries(doc, 'frames').map((x) => x.name), ...entries(doc, 'spheres').map((x) => x.name)];
    if (taken.some((n) => String(n).toLowerCase() === name.toLowerCase())) {
      return { error: `“${name}” adı zaten var: başka ad seç ya da önce sil.` };
    }
    const g = parseCenterRadius(f);
    if (g.error) return g;
    return { sphere: { type: SPHERE, name, center: g.center, radius: g.radius, tool_axis: g.tool_axis } };
  }

  /* Merkez önerisi: TCP konumu (m) + dz (mm) -> mm metni. */
  function centerFromTcp(position, dzMm = 0) {
    const p = (position || []).map(Number);
    if (p.length !== 3 || p.some((v) => !Number.isFinite(v))) return null;
    return { cx: fmtMm(p[0]), cy: fmtMm(p[1]), cz: (p[2] * 1000 + dzMm).toFixed(1) };
  }

  /* Erişim yanıtı -> gösterilecek metinler. Robotun `summary`/`note` metinleri AYNEN, bizimkiler ayrı satır. */
  function describeReach(doc) {
    const lines = [];
    const rng = doc.elevation_range_deg;
    lines.push(doc.reachable && Array.isArray(rng)
      ? `Erişilebilir yükselme: ${rng[0]}° … ${rng[1]}° (jog yükselmeyi en çok 85° ile sınırlar).`
      : 'Bu küre için erişilebilir hücre yok: bu merkez/yarıçap robotun şu anki pozundan kullanılamaz.');
    if (doc.summary) lines.push(String(doc.summary));
    const rows = (Array.isArray(doc.rows) ? doc.rows : []).map((r) =>
      `yükselme ${String(r.elevation_deg).padStart(4)}°: azimut ` +
      ((r.azimuth_intervals_deg || []).map(([a, b]) => `${a}…${b}°`).join(', ') || '—'));
    return { lines, rows, grid: gridLines(doc.grid), note: doc.note ? String(doc.note) : '',
             reachable: !!doc.reachable };
  }

  /* Kaba ızgara: satır = yükselme (üstte en yüksek), sütun = azimut; █ erişilebilir, · değil. */
  function gridLines(g) {
    if (!g || !Array.isArray(g.cells) || !Array.isArray(g.elevation_deg) || !Array.isArray(g.azimuth_deg)) return [];
    const out = [];
    for (let i = g.cells.length - 1; i >= 0; i--) {
      out.push(`${String(g.elevation_deg[i]).padStart(4)}° ` +
        Array.from(String(g.cells[i]), (c) => (c === '1' ? '█' : '·')).join(''));
    }
    const a = g.azimuth_deg;
    out.push(`      azimut ${a[0]}° → ${a[a.length - 1]}° (adım ${a.length > 1 ? a[1] - a[0] : '—'}°)`);
    return out;
  }

  const FrameTypes = { unknownTypes, typeError, canSphere, withSphere, withoutSphere, userBodies,
                       parseSphereForm, parseCenterRadius, centerFromTcp, describeReach, gridLines, num, toM, fmtMm,
                       UP_MM, AXES };
  if (typeof module !== 'undefined' && module.exports) module.exports = FrameTypes;
  if (typeof window === 'undefined') return;
  window.FrameTypes = FrameTypes;

  /* ---------------------------- arayüz ---------------------------- */
  const FT = FrameTypes;
  const W = { doc: null, snap: null, busy: false, reach: null, reachKey: null, confirmUnreachable: false,
              confirmDel: null };
  const q = (id) => document.getElementById(id);
  const key = (f) => JSON.stringify([f.cx, f.cy, f.cz, f.r, f.axis]);
  const form = () => ({ name: q('sw-name').value, cx: q('sw-cx').value, cy: q('sw-cy').value,
                        cz: q('sw-cz').value, r: q('sw-r').value, axis: q('sw-axis').value });

  function say(text, bad = false) {
    const n = q('sw-msg'); if (!n) return;
    n.textContent = text || ''; n.classList.toggle('bad', !!bad);
  }

  function field(label, id, props = {}) {
    return el('label', { class: 'jg-f sw-f', text: `${label} ` }, [
      el('input', { type: 'text', id, inputMode: 'decimal', autocomplete: 'off', 'aria-label': label, ...props })]);
  }

  function build() {
    const axis = el('select', { id: 'sw-axis', 'aria-label': 'Takım ekseni' });
    for (const a of FT.AXES) axis.append(el('option', { value: a, text: a === 'z' ? 'z (varsayılan)' : a }));
    const useTcp = el('button', { class: 'small', type: 'button', id: 'sw-tcp', text: 'Şu anki TCP’yi merkez al' });
    const useUp = el('button', { class: 'small', type: 'button', id: 'sw-up', text: 'TCP’nin 10 cm üstünü merkez al' });
    const reach = el('button', { class: 'small', type: 'button', id: 'sw-reach', text: 'Erişim aralığını hesapla' });
    const save = el('button', { class: 'small primary', type: 'button', id: 'sw-save', text: 'Küreyi kaydet' });
    useTcp.addEventListener('click', () => fillCenter(0));
    useUp.addEventListener('click', () => fillCenter(FT.UP_MM));
    reach.addEventListener('click', computeReach);
    save.addEventListener('click', saveSphere);
    const card = el('div', { class: 'card', id: 'p-sphere' }, [
      el('h2', { text: 'Küresel çerçeve (kubbe)' }),
      el('div', { class: 'muted', text: 'Merkez C ve yarıçap r tanımlar; jog (API) sağ-sol = azimut, yukarı-aşağı = ' +
        'yükselme, içeri-dışarı = r. Bu sihirbaz robotu hareket ettirmez. Küre ile jog bu Hub sürümünde YOK: ' +
        'yalnızca tanımlama.' }),
      el('ul', { class: 'fw-list', id: 'sw-list' }),
      el('details', { id: 'sw-new' }, [
        el('summary', { text: 'Yeni küre tanımla' }),
        el('div', { class: 'fw-form' }, [
          field('Ad', 'sw-name', { maxLength: 24, placeholder: 'örn. Kubbe1' }),
          el('div', { class: 'row' }, [useTcp, useUp]),
          el('div', { class: 'sw-center' }, [field('C x (mm)', 'sw-cx'), field('C y (mm)', 'sw-cy'), field('C z (mm)', 'sw-cz')]),
          el('div', { class: 'row' }, [field('Yarıçap r (mm)', 'sw-r', { placeholder: 'örn. 300' }),
            el('label', { class: 'jg-f', text: 'Takım ekseni (merkeze bakar) ' }, [axis])]),
          el('div', { class: 'row' }, [reach, save, el('button', { class: 'small', type: 'button', id: 'sw-clear', text: 'Temizle' })]),
          el('div', { class: 'say', id: 'sw-reach-out' }, [
            el('div', { id: 'sw-reach-lines' }), el('pre', { class: 'sw-grid', id: 'sw-reach-grid' }),
            el('pre', { class: 'sw-rows', id: 'sw-reach-rows' }), el('div', { class: 'muted', id: 'sw-reach-note' })])])]),
      el('div', { class: 'say jg-msg', id: 'sw-msg', role: 'alert' })]);
    q('panels').append(card);
    q('sw-clear').addEventListener('click', () => { for (const id of ['name', 'cx', 'cy', 'cz', 'r']) q(`sw-${id}`).value = ''; invalidate(); say(''); });
    for (const id of ['name', 'cx', 'cy', 'cz', 'r', 'axis']) q(`sw-${id}`).addEventListener('input', invalidate);
    q('sw-axis').addEventListener('change', invalidate);
  }

  function invalidate() {
    W.confirmUnreachable = false;
    const stale = W.reach && W.reachKey !== key(form());
    if (stale) { q('sw-reach-lines').textContent = 'Girdiler değişti: erişim aralığını yeniden hesapla.'; q('sw-reach-grid').textContent = ''; q('sw-reach-rows').textContent = ''; q('sw-reach-note').textContent = ''; W.reach = null; }
    render();
  }

  function fillCenter(dz) {
    const t = window.JogPanel && JogPanel.tcp();
    const c = t && FT.centerFromTcp(t.position, dz);
    if (!c) { say('TCP konumu okunamıyor: merkez alınamadı (eklem durumu yok).', true); return; }
    q('sw-cx').value = c.cx; q('sw-cy').value = c.cy; q('sw-cz').value = c.cz;
    say(dz ? `Merkez: TCP’nin ${dz / 10} cm üstü.` : 'Merkez: şu anki TCP.');
    invalidate();
  }

  async function computeReach() {
    if (W.busy) return;
    const f = form(), g = FT.parseCenterRadius(f);
    if (g.error) { say(g.error, true); return; }
    W.busy = true; render(); say('Erişim aralığı hesaplanıyor…');
    try {
      await JogPanel.release(false);                       // jog sürerken robot 409 verir
      const d = await api('/api/frames/reach', 'POST', { center: g.center, radius: g.radius, tool_axis: g.tool_axis });
      const r = FT.describeReach(d);
      W.reach = r; W.reachKey = key(f); W.confirmUnreachable = false;
      q('sw-reach-lines').replaceChildren(...r.lines.map((t, i) => el('div', { class: i === 0 && !r.reachable ? 'bad' : '', text: t })));
      q('sw-reach-grid').textContent = r.grid.join('\n');
      q('sw-reach-rows').textContent = r.rows.join('\n');
      q('sw-reach-note').textContent = r.note;
      say('');
    } catch (e) { say(window.JogPanel ? JogPanel.explain(e) : e.message, true); } finally { W.busy = false; render(); }
  }

  async function fresh() {
    const d = await api('/api/frames');
    W.doc = d;
    return d;
  }

  async function saveSphere() {
    if (W.busy) return;
    W.busy = true; render();
    try {
      const cur = await fresh();                           // taze liste: başka istemci değiştirmiş olabilir
      if (!FT.canSphere(cur)) { say('Robot küre çerçevesi desteklemiyor (types içinde sphere yok).', true); return; }
      const err = FT.typeError(cur); if (err) { say(err, true); return; }
      const p = FT.parseSphereForm(form(), cur);
      if (p.error) { say(p.error, true); return; }
      const known = W.reach && W.reachKey === key(form());
      if (known && !W.reach.reachable && !W.confirmUnreachable) {
        W.confirmUnreachable = true;
        say('Bu küreye şu an erişilemiyor. Yine de kaydetmek için “Yine de kaydet”e bas.', true);
        return;
      }
      await JogPanel.release(false);
      const d = await api('/api/frames', 'PUT', FT.withSphere(cur, p.sphere));
      W.doc = d; W.confirmUnreachable = false;
      update(d);
      if (window.FrameWizard) FrameWizard.reload(d);
      say(`“${p.sphere.name}” kaydedildi` + (known ? '.' : ' (erişim aralığı hesaplanmadan kaydedildi).'));
    } catch (e) { say(window.JogPanel && e.status !== undefined ? JogPanel.explain(e) : e.message, true); } finally { W.busy = false; render(); }
  }

  async function removeSphere(name) {
    if (W.confirmDel !== name) { W.confirmDel = name; render(); return; }
    W.confirmDel = null; W.busy = true; render();
    try {
      const cur = await fresh();
      const err = FT.typeError(cur); if (err) { say(err, true); return; }
      await JogPanel.release(false);
      const d = await api('/api/frames', 'PUT', FT.withoutSphere(cur, name));
      W.doc = d; update(d);
      if (window.FrameWizard) FrameWizard.reload(d);
      say(`“${name}” silindi.`);
    } catch (e) { say(window.JogPanel && e.status !== undefined ? JogPanel.explain(e) : e.message, true); } finally { W.busy = false; render(); }
  }

  /* GET/PUT yanıtı geldiğinde (frames.js çağırır): kart yalnızca robot `sphere` türünü biliyorsa vardır. */
  function update(doc) {
    W.doc = doc;
    const has = !!q('p-sphere'), off = q('p-sphere-off');
    if (!FT.canSphere(doc)) {
      if (has) q('p-sphere').remove();
      if (off) off.remove();
      if (doc && doc.spheres_error) {                     // bozuk küre dosyası: gizli kalma nedeni yazılır
        q('panels').append(el('div', { class: 'card', id: 'p-sphere-off' }, [
          el('h2', { text: 'Küresel çerçeve' }),
          el('div', { class: 'say bad', text: `Küre çerçeveleri robotta kapalı: ${doc.spheres_error}` })]));
      }
      return;
    }
    if (off) off.remove();
    if (!has) build();
    render();
  }

  function render() {
    if (!q('p-sphere') || !W.doc) return;
    const s = W.snap, doc = W.doc, ro = !!(s && (!s.me.can_control || s.estop.latched));
    const err = FT.typeError(doc);
    const list = q('sw-list'); list.replaceChildren();
    const sph = entries(doc, 'spheres');
    if (!sph.length) list.append(el('li', { class: 'muted', text: 'Kayıtlı küre yok.' }));
    for (const sp of sph) {
      const del = el('button', { class: 'small', type: 'button', disabled: W.busy || ro || !!err,
        text: W.confirmDel === sp.name ? 'Emin misin? Sil' : 'Sil' });
      del.addEventListener('click', () => removeSphere(sp.name));
      list.append(el('li', { class: 'fw-item' }, [el('b', { text: sp.name }),
        el('span', { class: 'muted', text: ` C(mm): ${(sp.center || []).map(fmtMm).join(', ')} · r: ${fmtMm(sp.radius)} mm · eksen ${sp.tool_axis || 'z'}` }), del]));
    }
    const max = doc.max_spheres || 0, full = max > 0 && sph.length >= max;
    const known = W.reach && W.reachKey === key(form());
    q('sw-tcp').disabled = W.busy || !!(s && !s.me.can_control);
    q('sw-up').disabled = q('sw-tcp').disabled;
    q('sw-reach').disabled = W.busy || ro;
    q('sw-save').disabled = W.busy || ro || full || !!err;
    q('sw-save').textContent = W.confirmUnreachable && known && !W.reach.reachable ? 'Yine de kaydet' : 'Küreyi kaydet';
    if (err) say(err, true);
    else if (full) say(`En çok ${max} küre: yenisi için birini sil.`, true);
  }

  function onState(s) { W.snap = s; render(); }
  window.SphereWizard = { update, onState };
})();
