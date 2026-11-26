/* A1 Algoritmalar sayfası: saf mantık + sahte DOM ile sayfa davranışı (tarayıcı gerekmez).
 *   node algo_page_check.js <algo.js>
 * Sınananlar: alan ayrıştırma (NaN/∞/üstel/onaltılık red, ondalık virgül), ihlalli satırın kırmızı olması ve ihlalin
 * HİÇ gizlenmemesi, 3B çizim planı (yol, işaret, kırmızı nokta/parça), sayfa akışı (üret -> denetle -> göster), eski
 * yanıtın yenisini ezmemesi, sunucu hata alanlarının işaretlenmesi, "Programa ekle" aktarımı, HubScene kimlik öneki,
 * sayfadan çıkınca temizlik, 3B yokken/reddedilince davranış. */
'use strict';
const assert = require('assert');
const fs = require('fs');

class N {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this._text = ''; this._cls = new Set();
    this.listeners = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.value = ''; this.attrs = {};
    this.classList = { toggle: (c, on) => { if (on === undefined ? !this._cls.has(c) : on) this._cls.add(c); else this._cls.delete(c); },
      add: (c) => this._cls.add(c), remove: (c) => this._cls.delete(c), contains: (c) => this._cls.has(c) }; }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  append(...c) { c.forEach((x) => { x.parent = this; this.children.push(x); }); }
  replaceChildren(...c) { this.children = []; this._text = ''; this.append(...c); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] !== undefined ? this.attrs[k] : this[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  fire(ev) { if (this.disabled) return; (this.listeners[ev] || []).forEach((fn) => fn({ target: this })); }
  click() { this.fire('click'); }
  set(v, ev = 'change') { this.value = v; this.fire(ev); }
}
global.window = global;
require(require('path').resolve(require('path').dirname(process.argv[2]), '..', 'pages.js'));   // gerçek HubPages.say (R8)
const realSay = global.HubPages.say;
global.el = (tag, props = {}, children = []) => {
  const e = new N(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'text') e.textContent = v; else if (k === 'class') e.className = v; else if (k.startsWith('data-')) e.dataset[k.slice(5)] = v; else e[k] = v; }
  e.append(...children); return e;
};
const walk = (n, fn) => { fn(n); n.children.forEach((c) => walk(c, fn)); };
const all = (root, pred) => { const out = []; walk(root, (n) => { if (pred(n)) out.push(n); }); return out; };
const btn = (root, text) => all(root, (n) => n.tagName === 'BUTTON' && n.textContent === text)[0];
const byTitle = (root, title) => all(root, (n) => n.title === title)[0];
const flush = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const rows = (root) => all(root, (n) => n.className.includes('al-step'));

/* Gerçek HubScene gibi davranır: kimlik "<sayfa>:<ad>", tür başına geometri doğrulaması, fırlatmaz {ok:false} döner. */
const ID_RE = /^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,64}$/;
function fakeScene({ implemented = true, reject = null } = {}) {
  const objs = new Map(), no = (error, message) => ({ ok: false, error, message });
  return { implemented, objs, adds: [],
    addObject(o) {
      if (reject) return no('bad_request', reject);
      if (typeof o.id !== 'string' || !ID_RE.test(o.id)) return no('bad_request', `kimlik geçersiz: ${o.id}`);
      if (objs.has(o.id)) return no('exists', `nesne zaten var: ${o.id}`);
      const g = o.geometry || {};
      if (o.kind === 'marker' && !(g.radius_m > 0)) return no('bad_request', 'işaret yarıçapı');
      if (o.kind === 'polyline' && !(Array.isArray(g.points_m) && g.points_m.length >= 2)) return no('bad_request', 'poliçizgi 2+ nokta');
      objs.set(o.id, o); this.adds.push(o.id); return { ok: true, id: o.id };
    },
    removeObject(id) { if (!objs.has(id)) return no('not_found', id); objs.delete(id); return { ok: true }; } };
}

const DEFAULTS = (fields) => Object.fromEntries(fields.map((f) => [f.key, f.default]));
const F = (key, label, def, min, max, integer = false) => ({ key, label, unit: 'mm', default: def, min, max, integer });
const KINDS = [
  { id: 'grid', title: 'Izgara / palet pick-place', about: 'Izgara hakkında', fields: [F('rows', 'Satır', 2, 1, 20, true), F('pitch_mm', 'Aralık', 20, 1, 500)] },
  { id: 'arc', title: 'Daire / yay', about: 'Daire hakkında', fields: [{ key: 'plane', label: 'Düzlem', unit: '', default: 'xy', options: [{ value: 'xy', label: 'XY' }, { value: 'xz', label: 'XZ' }] },
    F('radius_mm', 'Yarıçap', 40, 1, 500)] },
];
const mv = (i, x, y, z) => ({ kind: 'move_l', pos_m: [x, y, z], orientation: 'free', _i: i });
const okRow = (line) => ({ line, sub: null, kind: 'move_l', ok: true, violations: [] });
const badRow = (line, kind = 'outside_safe_zone', message = 'hedef güvenli kutunun dışında') => ({ line, sub: null, kind: 'move_l', ok: false, violations: [{ kind, at_pct: 100, message }] });
function result({ bad = [], mock = false, extra = {} } = {}) {
  const steps = [{ kind: 'tool', open_pct: 100 }, { kind: 'comment', text: 'Hücre 1' }, mv(2, 0.12, -0.06, 0.08), mv(3, 0.12, -0.06, 0.05), mv(4, 0.1, 0.12, 0.08)];
  steps.forEach((s) => delete s._i);
  return { ok: true, kind: 'grid', count: steps.length, steps, marks: [{ i: 3, n: null, role: 'pick' }, { i: 4, n: 1, role: 'place' }],
    points: [2, 3, 4].map((i) => ({ i, pos_m: steps[i].pos_m })),
    check: { ok: bad.length === 0, violations: bad.length, rows: [okRow(0), okRow(2), okRow(3), ...bad.map((i) => badRow(i))].filter((r) => !(bad.includes(r.line) && r.ok)) },
    backend: mock ? { label: 'SAHTE (mock)', measured: false, mock: true } : { label: 'SİM', measured: false, mock: false }, ...extra };
}

function load({ gen, kinds = KINDS, kindsFail = false, scene = false, program = 'ok' } = {}) {
  const calls = [], toasts = [], added = [], names = [];
  global.api = async (path, method = 'GET', body) => {
    calls.push({ path, method, body });
    if (path === '/api/algo/kinds') { if (kindsFail) throw Object.assign(new Error('Sunucu yok'), { status: 500, body: {} }); return { ok: true, kinds, limits: { max_steps: 500 } }; }
    if (path === '/api/algo/generate') return gen(body, calls.length);
    throw Object.assign(new Error(`beklenmeyen çağrı ${method} ${path}`), { status: 404, body: {} });
  };
  global.toast = (msg, bad) => toasts.push({ msg, bad: !!bad });
  if (scene) global.HubScene = fakeScene(scene === true ? {} : scene); else delete global.HubScene;
  if (program === 'ok') global.HubProgram = { addSteps: (s, n) => { added.push(s); names.push(n); return { ok: true, total: 40 + s.length }; } };
  else if (program === 'refuse') global.HubProgram = { addSteps: () => ({ ok: false, message: 'Program çalışıyor: bitince ekle' }) };
  else delete global.HubProgram;
  let def = null;
  global.HubPages = { register(d) { def = d; }, mountView: async () => {}, say: realSay };
  new Function(fs.readFileSync(process.argv[2], 'utf8'))();
  return { def, calls, toasts, added, names, H: global.HubScene };
}
const snap = (o = {}) => ({ me: { can_control: true }, ...o });
const gens = (calls) => calls.filter((c) => c.path === '/api/algo/generate');

(async () => {
  /* ---- 1) saf mantık ---- */
  const L = load({ gen: () => result() }).def.logic, num = F('x', 'x', 5, -10, 10), int = F('n', 'n', 2, 1, 20, true);
  assert.deepStrictEqual(L.parse(num, '7.5'), { ok: true, value: 7.5 });
  assert.deepStrictEqual(L.parse(num, ' 12,5 '.replace('12', '7')), { ok: true, value: 7.5 }, 'ondalık virgül (Türkçe klavye)');
  assert.deepStrictEqual(L.parse(num, '-3'), { ok: true, value: -3 });
  assert.deepStrictEqual(L.parse(num, '.5'), { ok: true, value: 0.5 });
  for (const bad of ['', '  ', 'abc', 'NaN', 'Infinity', '-Infinity', '1e3', '1e1', '1E1', '0x10', '0x5', '0b1', '5e-1', '--1', '1.2.3', '1,2,3', '5 mm', '١٢']) assert.strictEqual(L.parse(num, bad).ok, false, `reddedilmeli: "${bad}"`);
  assert.strictEqual(L.parse(num, '10.0001').ok, false); assert.strictEqual(L.parse(num, '-10.1').ok, false);
  assert.match(L.parse(num, '11').why, /-10 … 10/);
  assert.strictEqual(L.parse(int, '2.5').why, 'tamsayı olmalı'); assert.strictEqual(L.parse(int, '3').ok, true); assert.strictEqual(L.parse(int, '0').ok, false);
  const sel = KINDS[1].fields[0];
  assert.strictEqual(L.parse(sel, 'xz').ok, true); assert.strictEqual(L.parse(sel, 'ab').ok, false);
  assert.strictEqual(L.describe({ kind: 'move_l', pos_m: [0.12, -0.06, 0.0805], orientation: 'free' }), 'x 120.0 · y -60.0 · z 80.5 mm · yönelim serbest');
  assert.strictEqual(L.describe({ kind: 'move_l', pos_m: [0.1, 0, 0.1], rpy_deg: [0, 0, 0] }), 'x 100.0 · y 0.0 · z 100.0 mm');
  assert.strictEqual(L.programName('Izgara / palet pick-place'), 'Izgara palet pick-place', 'yasak karakter boşluk olur, boşluklar birleşir');
  assert.strictEqual(L.programName('Daire / yay'), 'Daire yay'); assert.strictEqual(L.programName('Şerit-tarama_1.2'), 'Şerit-tarama_1.2');
  assert.strictEqual(L.programName('///'), 'Algoritma', 'boş kalırsa varsayılan'); assert.strictEqual(L.programName('x'.repeat(60)).length, 40);
  assert.ok(/^[0-9A-Za-zÇĞİÖŞÜçğıöşü][0-9A-Za-zÇĞİÖŞÜçğıöşü _.-]{0,39}$/.test(L.programName('(Zigzag) tarama!')), 'program_store.NAME_RE ile uyumlu');
  assert.strictEqual(L.describe({ kind: 'tool', open_pct: 30 }), '%30 açık'); assert.strictEqual(L.describe({ kind: 'comment', text: 'Hücre 1' }), 'Hücre 1');
  assert.match(L.summary(result()), /✓ 3 hareket\/alet adımı güvenli kutuda/); assert.match(L.summary(result({ bad: [4] })), /^5 adım · ✗ 1 ihlal \(adım 5\): kırmızı satırlar$/, 'ihlalli adım numarası (1\'den) özette');
  const many = { count: 30, check: { violations: 8, rows: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => badRow(i * 3)) } };
  assert.strictEqual(L.summary(many), '30 adım · ✗ 8 ihlal (adım 4, 7, 10, 13, 16, 19 …): kırmızı satırlar', 'çok ihlalde ilk 6 numara + …');
  // satır görünürlüğü: ihlal asla gizlenmez
  const long = { steps: Array.from({ length: 200 }, (_, i) => ({ i })), check: { rows: [okRow(1), badRow(150), badRow(199)] } };
  const vis = L.visible(long);
  assert.strictEqual(vis.idx.length, 62); assert.ok(vis.idx.includes(150) && vis.idx.includes(199), 'ihlalli satırlar 60. satırın ötesinde de listelenir');
  assert.strictEqual(vis.hidden, 138); assert.deepStrictEqual(vis.idx, [...vis.idx].sort((a, b) => a - b));
  assert.strictEqual(L.visible({ steps: [{}, {}], check: { rows: [] } }).hidden, 0);
  // 3B planı
  const plan = L.scene(result({ bad: [4] }));
  assert.deepStrictEqual(plan.path, [[0.12, -0.06, 0.08], [0.12, -0.06, 0.05], [0.1, 0.12, 0.08]]);
  assert.deepStrictEqual(plan.marks.map((m) => [m.id, m.number, m.color]), [['algo:m3', null, '#e3b341'], ['algo:m4', 1, '#f85149']], 'seçili rol rengi; ihlalli işaret KIRMIZI');
  assert.strictEqual(plan.bad.length, 1); assert.strictEqual(plan.bad[0].marker, false, 'zaten işaretli nokta ikinci kez işaretlenmez');
  assert.deepStrictEqual(plan.bad[0].seg, { id: 'algo:s4', pts: [[0.12, -0.06, 0.05], [0.1, 0.12, 0.08]] }, 'ihlalli parça: önceki noktadan bu noktaya');
  const plain = L.scene(result({ bad: [2] }));
  assert.strictEqual(plain.bad[0].marker, true, 'işaretsiz ihlalli nokta kırmızı işaret alır'); assert.strictEqual(plain.bad[0].seg, null, 'ilk noktanın önceki noktası yok');
  assert.strictEqual(L.scene(result()).bad.length, 0);
  assert.strictEqual(L.scene({ ...result(), points: [result().points[0]] }).path, null, 'tek nokta: çizgi yok');
  const manyPts = Array.from({ length: 300 }, (_, i) => ({ i, pos_m: [0, 0, i / 1000] }));
  const manyBad = L.scene({ steps: manyPts.map((p) => ({ pos_m: p.pos_m })), marks: [], points: manyPts, check: { rows: manyPts.map((p) => badRow(p.i)) } });
  assert.strictEqual(manyBad.bad.length, 40, 'kırmızı nokta sayısı sınırlı');
  assert.strictEqual(L.scene({ steps: manyPts.map((p) => ({ pos_m: p.pos_m })), marks: manyPts.map((p) => ({ i: p.i, n: p.i, role: 'line' })), points: manyPts, check: { rows: [] } }).marks.length, 80);

  /* ---- 2) sayfa: üret -> denetle -> göster ---- */
  let reply = () => result();
  const a = load({ gen: (b, k) => reply(b, k) });
  const root = new N('section'); await a.def.mount(root); await flush(); a.def.onState(snap());
  assert.strictEqual(a.calls[0].path, '/api/algo/kinds');
  const g0 = gens(a.calls)[0];
  assert.deepStrictEqual(g0.body, { kind: 'grid', params: { rows: 2, pitch_mm: 20 }, speed_pct: 25 }, 'varsayılan parametreler sunucudaki tanımdan');
  assert.strictEqual(a.def.id, 'algo'); assert.ok(!root.textContent.includes('undefined'));
  assert.match(root.textContent, /✓ 3 hareket\/alet adımı güvenli kutuda/);
  assert.strictEqual(rows(root).length, 5); assert.strictEqual(rows(root).filter((r) => r.className.includes('bad')).length, 0);
  assert.strictEqual(rows(root).filter((r) => r.className.includes('ok')).length, 3, 'denetimi geçen satırlar yeşil kenarlı');
  assert.strictEqual(btn(root, 'Programa ekle').disabled, false);
  assert.strictEqual(all(root, (n) => n.className === 'banner')[0].hidden, true, 'SİM: SAHTE bandı yok');
  assert.ok(byTitle(root, 'Satır').value === '2' && byTitle(root, 'Aralık (mm)') === undefined, 'form alanları tanımdan; etiket başlığı ham etiket');

  // alan düzenleme: geçersiz değer istek GÖNDERMEZ; geçerli değer 350 ms sonra tek istek
  const n0 = gens(a.calls).length, pitch = byTitle(root, 'Aralık');
  pitch.set('abc');
  assert.match(root.textContent, /Geçersiz değer — Aralık: sayı olmalı/, 'geçersiz değerin nedeni HEMEN yazılır (350 ms beklenmez)');
  await flush(450);
  assert.strictEqual(gens(a.calls).length, n0, 'geçersiz değer: istek yok'); assert.ok(pitch.className.includes('bad'), 'alan kırmızı');
  assert.strictEqual(btn(root, 'Programa ekle').disabled, true, 'sonuç geçersiz: Programa ekle kapalı');
  pitch.set('30,5'); pitch.set('31');
  assert.match(root.textContent, /Değer değişti: yeniden denetleniyor/, 'değişince eski sonucun neden kapandığı hemen yazılır');
  assert.strictEqual(btn(root, 'Programa ekle').disabled, true, 'sonuç eskirken ekleme kapalı'); await flush(450);
  assert.strictEqual(gens(a.calls).length, n0 + 1, 'ardışık düzenleme tek isteğe birleşir (debounce)');
  assert.strictEqual(gens(a.calls).pop().body.params.pitch_mm, 31); assert.ok(!pitch.className.includes('bad'));
  await flush(); assert.strictEqual(btn(root, 'Programa ekle').disabled, false);
  byTitle(root, 'Satır').set('2.5'); await flush(450);
  assert.match(root.textContent, /Geçersiz değer — Satır: tamsayı olmalı/, 'hata metni görünür');
  assert.strictEqual(btn(root, 'Programa ekle').disabled, true, 'alan geçersizken eski sonuç eklenemez');
  byTitle(root, 'Satır').set('2'); await flush(450);                     // geçerli değere dönünce sonuç yeniden üretilir
  assert.ok(!byTitle(root, 'Satır').className.includes('bad')); assert.strictEqual(btn(root, 'Programa ekle').disabled, false);

  // eski yanıt yenisini ezmez
  // yavaş ESKİ istek, hızlı YENİ isteğin ardından döner (350 ms debounce'dan uzun): sonuç yenisi olmalı
  const slow = load({ gen: async (b) => { if (b.params.pitch_mm === 20) { await flush(700); return result({ extra: { count: 111 } }); } return result({ extra: { count: 222 } }); } });
  const rs = new N('section'); await slow.def.mount(rs); slow.def.onState(snap());
  byTitle(rs, 'Aralık').set('25'); await flush(500);
  assert.match(rs.textContent, /222 adım/, 'yeni yanıt göründü'); await flush(500);
  assert.match(rs.textContent, /222 adım/); assert.ok(!/111 adım/.test(rs.textContent), 'yavaş eski yanıt yenisini ezmedi');

  // ihlal: kırmızı satır + neden + özet; adımlar yine de listelenir
  reply = () => result({ bad: [4] });
  const b = load({ gen: () => result({ bad: [4] }) }); const rb = new N('section'); await b.def.mount(rb); await flush(); b.def.onState(snap());
  const red = rows(rb).filter((r) => r.className.includes('bad'));
  assert.strictEqual(red.length, 1); assert.strictEqual(red[0].dataset.line, '4', 'kırmızı olan, ihlalli adımın kendisi');
  assert.match(red[0].textContent, /hedef güvenli kutunun dışında/); assert.match(rb.textContent, /✗ 1 ihlal \(adım 5\): kırmızı satırlar/);
  assert.match(rb.textContent, /İhlalli 1 adım da eklenir/, 'ihlalliyi eklemenin sonucu açıkça söylenir');
  assert.strictEqual(btn(rb, 'Programa ekle').disabled, false);

  // sunucu parametre hatası: ilgili alan kırmızı, mesaj görünür
  const inv = load({ gen: () => { throw Object.assign(new Error('Parametre geçersiz'), { status: 422, body: { error: 'invalid_params', errors: [{ field: 'pitch_mm', message: 'x' }] } }); } });
  const ri = new N('section'); await inv.def.mount(ri); await flush();
  assert.ok(byTitle(ri, 'Aralık').className.includes('bad'), 'sunucunun reddettiği alan kırmızı'); assert.match(ri.textContent, /Parametre geçersiz/);
  assert.strictEqual(btn(ri, 'Programa ekle').disabled, true); assert.strictEqual(rows(ri).length, 0);

  // üreteç değiştirme
  const rc = new N('section'); const c = load({ gen: (body) => result({ extra: { kind: body.kind } }) }); await c.def.mount(rc); await flush();
  btn(rc, 'Daire / yay').click(); await flush();
  assert.strictEqual(gens(c.calls).pop().body.kind, 'arc'); assert.deepStrictEqual(gens(c.calls).pop().body.params, { plane: 'xy', radius_mm: 40 });
  assert.ok(byTitle(rc, 'Düzlem') && byTitle(rc, 'Düzlem').tagName === 'SELECT', 'seçimli alan <select>'); assert.strictEqual(byTitle(rc, 'Satır'), undefined);
  assert.strictEqual(btn(rc, 'Daire / yay').attrs['aria-pressed'], 'true'); assert.strictEqual(btn(rc, 'Izgara / palet pick-place').attrs['aria-pressed'], 'false');
  btn(rc, 'Izgara / palet pick-place').click(); await flush();
  assert.strictEqual(byTitle(rc, 'Satır').value, '2', 'üreteçler arası değerler korunur');

  // mock bandı
  const m = load({ gen: () => result({ mock: true }) }); const rm = new N('section'); await m.def.mount(rm); await flush();
  assert.strictEqual(all(rm, (n) => n.className === 'banner')[0].hidden, false); assert.match(rm.textContent, /SAHTE \(mock\) hareket servisi: ölçüm değildir/);

  /* ---- 3) Programa ekle (her load() genel HubProgram'ı değiştirir: bu bölüm kendi yüklemesini kullanır) ---- */
  const ad = load({ gen: () => result() }), root3 = new N('section'); await ad.def.mount(root3); await flush(); ad.def.onState(snap());
  btn(root3, 'Programa ekle').click();
  assert.strictEqual(ad.added.length, 1); assert.deepStrictEqual(ad.added[0], result().steps, 'üretilen adımlar AYNEN aktarılır');
  assert.strictEqual(ad.names[0], 'Izgara palet pick-place', 'adsız programa verilecek varsayılan ad aktarılır');
  assert.match(root3.textContent, /5 adım Program'a eklendi \(toplam 45\)/);
  assert.strictEqual(all(root3, (n) => n.href === '#/program').length, 1, 'Program sayfasına bağlantı');
  assert.strictEqual(all(root3, (n) => n.href === '#/program')[0].parent.hidden, false, 'bağlantı satırı görünür');
  assert.ok(ad.toasts.some((t) => !t.bad && /Program'a eklendi/.test(t.msg)));
  for (let k = 0; k < 3; k++) ad.def.onState(snap());                      // durum yoklaması her ~1 sn yeniden çizer
  assert.match(root3.textContent, /5 adım Program'a eklendi/, 'onay satırı durum yoklamasıyla SİLİNMEZ');
  assert.strictEqual(all(root3, (n) => n.href === '#/program')[0].parent.hidden, false);
  byTitle(root3, 'Aralık').set('25');
  assert.strictEqual(all(root3, (n) => n.href === '#/program')[0].parent.hidden, true, 'parametre değişince eski onay HEMEN kalkar');
  await flush(450);
  assert.strictEqual(btn(root3, 'Programa ekle').disabled, false);
  const rf = new N('section'); const f = load({ gen: () => result(), program: 'refuse' }); await f.def.mount(rf); await flush();
  btn(rf, 'Programa ekle').click(); assert.ok(f.toasts.some((t) => t.bad && /Program çalışıyor/.test(t.msg)), 'Program reddederse kullanıcı görür');
  assert.ok(!/Program'a eklendi/.test(rf.textContent), 'reddedilince "eklendi" yazılmaz');
  const rn = new N('section'); const none = load({ gen: () => result(), program: 'none' }); await none.def.mount(rn); await flush();
  btn(rn, 'Programa ekle').click(); assert.ok(none.toasts.some((t) => t.bad && /yüklenemedi/.test(t.msg)), 'HubProgram yoksa sessiz kalmaz');
  ad.def.onState(snap({ me: { can_control: false } })); assert.strictEqual(btn(root3, 'Programa ekle').disabled, true, 'kontrol yetkisi yoksa ekleme kapalı');
  ad.def.onState(snap()); assert.strictEqual(btn(root3, 'Programa ekle').disabled, false);

  /* ---- 4) HubScene: kimlik öneki `algo:`, yol + işaretler, ihlal kırmızı, çıkışta temizlik ---- */
  const s = load({ gen: () => result({ bad: [4] }), scene: true }), rsc = new N('section'); await s.def.mount(rsc); await flush(); s.def.onState(snap());
  const S = s.H, ids = [...S.objs.keys()];
  assert.ok(ids.length >= 4 && ids.every((id) => id.startsWith('algo:')), 'yalnız algo: kimlikleri');
  assert.strictEqual(S.objs.get('algo:path').kind, 'polyline'); assert.strictEqual(S.objs.get('algo:path').geometry.points_m.length, 3);
  assert.strictEqual(S.objs.get('algo:m4').color, '#f85149', 'ihlalli işaret kırmızı'); assert.strictEqual(S.objs.get('algo:m4').geometry.number, 1);
  assert.strictEqual(S.objs.get('algo:m3').geometry.number, undefined, 'numarasız işaret number taşımaz');
  assert.strictEqual(S.objs.get('algo:s4').color, '#f85149', 'ihlalli parça kırmızı çizgi'); assert.strictEqual(S.objs.get('algo:path').color, '#a371f7');
  assert.strictEqual(rsc.textContent.includes('3B sahne:'), false);
  s.def.onHide(); assert.strictEqual(S.objs.size, 0, 'sayfadan çıkınca TÜM nesneler kalkar');
  s.def.onShow(); await flush(50); assert.ok(S.objs.has('algo:path') && S.objs.has('algo:m4'), 'sayfaya dönünce yeniden çizilir ve doğrulama yenilenir');
  assert.ok(gens(s.calls).length >= 2, 'geri dönünce robot başka yerde olabilir: yeniden denetlenir');
  byTitle(rsc, 'Aralık').set('25'); await flush(500); assert.ok([...S.objs.keys()].every((id) => id.startsWith('algo:')) && S.objs.size === ids.length, 'parametre değişince eski çizim silinir, yenisi gelir (kopya yok)');
  const off = load({ gen: () => result(), scene: { implemented: false } }), ro = new N('section'); await off.def.mount(ro); await flush();
  assert.strictEqual(off.H.adds.length, 0, 'WebGL yoksa sahneye dokunulmaz'); assert.strictEqual(rows(ro).length, 5, 'liste yine çalışır');
  const rej = load({ gen: () => result(), scene: { reject: 'kimlik geçersiz' } }), rr = new N('section'); await rej.def.mount(rr); await flush();
  assert.match(rr.textContent, /3B sahne: kimlik geçersiz/, 'sahne {ok:false} dönerse kullanıcı görür (sessiz çizmeme yok)');

  /* ---- 5) üreteç listesi alınamazsa ---- */
  const dead = load({ gen: () => result(), kindsFail: true }), rd = new N('section'); await dead.def.mount(rd);
  assert.match(rd.textContent, /Üreteçler alınamadı: Sunucu yok/); dead.def.onHide(); dead.def.onShow(); dead.def.onState(snap());
  console.log('ALGO_PAGE_OK'); process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
