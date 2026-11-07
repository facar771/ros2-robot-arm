/* L8 küre sihirbazı: gerçek sphere.js ve frames.js'i küçük bir SAHTE DOM (jsdom yok) üzerinde çalıştırır.
 * pytest çağırır (tests/test_sphere_wizard.py); node yoksa o test atlanır. Çıkış 0 = geçti.
 *   node sphere_wizard_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);

/* ---- sahte DOM: yalnızca web kodunun kullandığı kadarı ---- */
class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.parent = null; this.className = '';
    this.value = ''; this.disabled = false; this._text = ''; this.dataset = {}; this.style = { setProperty() {} };
    this.classList = { toggle: (c, on) => { const s = new Set(this.className.split(' ').filter(Boolean)); (on ? s.add(c) : s.delete(c)); this.className = [...s].join(' '); } }; }
  append(...c) { for (const x of c) { const n = typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x; n.parent = this; this.children.push(n); } }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute() {}
  async click() { if (this.disabled) return; for (const f of this.listeners.click || []) await f({ target: this }); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
}
const body = new N('body'), panels = Object.assign(new N('div'), { id: 'panels' });
body.append(panels);
const walk = (n, id) => { if (n.id === id) return n; for (const c of n.children) { const r = walk(c, id); if (r) return r; } return null; };
global.window = global;
global.document = { createElement: (t) => new N(t), getElementById: (id) => walk(body, id), createTextNode: (t) => Object.assign(new N('#text'), { _text: t }) };
const byId = (id) => document.getElementById(id);
/* app.js'teki el() ile AYNI (app.js DOM'a bağlı olduğu için burada kopya) */
global.el = function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
    else if (k === 'style') for (const [sk, sv] of Object.entries(v)) e.style.setProperty(sk, sv);
    else if (k.startsWith('data-')) e.setAttribute(k, v); else e[k] = v;
  }
  for (const c of children) e.append(c);
  return e;
};

/* ---- sahte api() ve JogPanel ---- */
let calls = [], handler = null;
global.api = async (p, m = 'GET', b) => { calls.push({ p, m, b }); return handler(p, m, b); };
class ApiError extends Error { constructor(status, body) { super(body && body.message || `HTTP ${status}`); this.status = status; this.body = body || {}; } }
let tcpPos = [0.3, 0.1, 0.4];
global.JogPanel = { tcp: () => ({ position: tcpPos.slice() }), explain: (e) => `HATA ${e.status}: ${e.message}`,
                    release: async () => {}, refreshFrames: async () => {}, frame: () => 'World', setFrame() {} };

const FT = require(path.join(WEB, 'sphere.js'));
require(path.join(WEB, 'frames.js'));

const pts = (o) => ({ origin: o, x: [o[0] + 0.1, o[1], o[2]], xy: [o[0], o[1] + 0.1, o[2]] });
const userFrame = (name) => ({ type: 'user', name, points: pts([0.3, 0, 0.1]), pose: { position: [0.3, 0, 0.1] }, wpr_deg: [0, 0, 0], warnings: [] });
const sphereDoc = { type: 'sphere', name: 'Eski', center: [0.2, 0, 0.3], radius: 0.4, tool_axis: 'z' };
const docOf = (extra = {}) => ({ builtin: ['World', 'Tool'], max_frames: 10, types: ['user', 'sphere'], frames: [], spheres: [], max_spheres: 10, ...extra });
const reachOk = { ok: true, reachable: true, elevation_range_deg: [10, 60], summary: 'özet metni', note: 'çarpışma modeli YOK',
                  rows: [{ elevation_deg: 10, azimuth_intervals_deg: [[0, 90]] }],
                  grid: { azimuth_deg: [0, 15, 30], elevation_deg: [10, 20], cells: ['110', '011'] } };

function reset(doc) {
  panels.replaceChildren(); calls = [];
  delete global.FrameWizardMounted;
}
async function mountWith(getDoc, extraHandler) {
  panels.replaceChildren(); calls = [];
  handler = async (p, m, b) => {
    if (extraHandler) { const r = await extraHandler(p, m, b); if (r !== undefined) return r; }
    if (p === '/api/frames' && m === 'GET') return JSON.parse(JSON.stringify(getDoc()));
    throw new Error(`beklenmeyen çağrı ${m} ${p}`);
  };
  // FrameWizard tek seferlik mount: durumu sıfırlamak için modülleri yeniden yükle
  for (const f of ['sphere.js', 'frames.js']) delete require.cache[require.resolve(path.join(WEB, f))];
  require(path.join(WEB, 'sphere.js')); require(path.join(WEB, 'frames.js'));
  await FrameWizard.mount();
  await new Promise((r) => setImmediate(r));
}
const puts = () => calls.filter((c) => c.m === 'PUT');

(async () => {
  /* 1) saf mantık */
  assert.deepStrictEqual(FT.unknownTypes(docOf({ frames: [userFrame('A')], spheres: [sphereDoc] })), []);
  assert.deepStrictEqual(FT.unknownTypes({ frames: [{ name: 'Eski', points: {} }] }), []);                  // type yok = user
  assert.ok(/cylinder/.test(FT.typeError(docOf({ frames: [{ type: 'cylinder', name: 'S' }] }))));
  assert.ok(/spheres\[0\]/.test(FT.typeError(docOf({ spheres: [{ type: 'cone', name: 'K' }] }))));
  assert.strictEqual(FT.canSphere({ types: ['user', 'sphere'] }), true);
  assert.strictEqual(FT.canSphere({ types: ['user'] }), false);
  assert.strictEqual(FT.canSphere({}), false);                                                              // eski robot: types yok
  const cur = docOf({ frames: [userFrame('A')], spheres: [sphereDoc] });
  const nw = { type: 'sphere', name: 'Yeni', center: [0, 0, 0.5], radius: 0.3, tool_axis: 'z' };
  assert.deepStrictEqual(FT.withSphere(cur, nw), { frames: [{ name: 'A', points: cur.frames[0].points }], spheres: [sphereDoc, nw] });
  assert.deepStrictEqual(FT.withoutSphere(cur, 'Eski'), { frames: [{ name: 'A', points: cur.frames[0].points }], spheres: [] });
  assert.throws(() => FT.withSphere(docOf({ frames: [{ type: 'cylinder', name: 'S' }] }), nw), /cylinder/);
  const f = { name: 'K1', cx: '300', cy: '0,5', cz: '500', r: '123.4', axis: '-z' };
  assert.deepStrictEqual(FT.parseSphereForm(f, cur).sphere, { type: 'sphere', name: 'K1', center: [0.3, 0.0005, 0.5], radius: 0.1234, tool_axis: '-z' });
  for (const bad of [{ ...f, name: '1x' }, { ...f, name: 'world' }, { ...f, name: 'a' }, { ...f, name: 'eski' }, { ...f, cx: '' }, { ...f, r: 'abc' }]) {
    const r = FT.parseSphereForm(bad, docOf({ frames: [userFrame('a')], spheres: [sphereDoc] }));
    assert.ok(r.error, JSON.stringify(bad));
  }
  assert.deepStrictEqual(FT.centerFromTcp([0.3, 0.1, 0.4], 100), { cx: '300.0', cy: '100.0', cz: '500.0' });
  assert.strictEqual(FT.centerFromTcp(null), null);
  const d = FT.describeReach(reachOk);
  assert.ok(d.lines[0].includes('10° … 60°') && d.lines[1] === 'özet metni' && d.note === 'çarpışma modeli YOK');
  assert.strictEqual(d.grid[0].trim().startsWith('20°'), true);                                              // üstte en yüksek yükselme
  assert.ok(FT.describeReach({ reachable: false, elevation_range_deg: null }).lines[0].includes('yok'));

  /* 2) sphere desteği YOK -> seçenek yok (eski robot: types yok / yalnız user) */
  for (const old of [() => docOf({ types: ['user'], spheres: [], max_spheres: 0 }),
                     () => ({ builtin: ['World', 'Tool'], max_frames: 10, frames: [userFrame('A')] })]) {
    await mountWith(old);
    assert.ok(byId('p-frames'), 'düzlem kartı olmalı');
    assert.strictEqual(byId('p-sphere'), null, 'sphere yokken küre kartı OLMAMALI');
    assert.strictEqual(byId('sw-save'), null);
  }
  await mountWith(() => docOf({ types: ['user'], spheres_error: 'YAMLError: bozuk' }));
  assert.strictEqual(byId('p-sphere'), null);
  assert.ok(byId('p-sphere-off').textContent.includes('bozuk'), 'gizli kalma nedeni yazılmalı');

  /* 3) sphere desteği VAR: merkez + r -> erişim aralığı -> kaydet; diğer türler KORUNUR */
  let live = docOf({ frames: [userFrame('Tezgah')], spheres: [sphereDoc] });
  const ext = async (p, m, b) => {
    if (p === '/api/frames/reach') return reachOk;
    if (p === '/api/frames' && m === 'PUT') { live = { ...live, frames: b.frames.map((x) => ({ type: 'user', ...x })), spheres: b.spheres === undefined ? live.spheres : b.spheres }; return JSON.parse(JSON.stringify(live)); }
  };
  await mountWith(() => live, ext);
  assert.ok(byId('p-sphere'), 'types içinde sphere varken küre kartı olmalı');
  assert.ok(byId('sw-list').textContent.includes('Eski'), 'mevcut küre listelenmeli');
  await byId('sw-up').click();
  assert.deepStrictEqual([byId('sw-cx').value, byId('sw-cy').value, byId('sw-cz').value], ['300.0', '100.0', '500.0']);
  await byId('sw-tcp').click();
  assert.strictEqual(byId('sw-cz').value, '400.0');
  byId('sw-cz').value = '500'; byId('sw-name').value = 'Kubbe1'; byId('sw-r').value = '250';
  await byId('sw-reach').click();
  const rc = calls.find((c) => c.p === '/api/frames/reach');
  assert.deepStrictEqual(rc.b, { center: [0.3, 0.1, 0.5], radius: 0.25, tool_axis: 'z' });
  assert.ok(byId('sw-reach-lines').textContent.includes('10° … 60°') && byId('sw-reach-grid').textContent.includes('█'));
  await byId('sw-save').click();
  assert.strictEqual(puts().length, 1, byId('sw-msg').textContent);
  assert.deepStrictEqual(puts()[0].b, { frames: [{ name: 'Tezgah', points: live.frames[0].points }],
    spheres: [sphereDoc, { type: 'sphere', name: 'Kubbe1', center: [0.3, 0.1, 0.5], radius: 0.25, tool_axis: 'z' }] });
  assert.deepStrictEqual(live.spheres.map((s) => s.name), ['Eski', 'Kubbe1']);
  assert.deepStrictEqual(live.frames.map((s) => s.name), ['Tezgah']);                                        // düzlem silinmedi
  assert.ok(byId('sw-msg').textContent.includes('kaydedildi') && !byId('sw-msg').textContent.includes('hesaplanmadan'));
  assert.ok(byId('sw-list').textContent.includes('Kubbe1'));

  /* 3b) girdi değişince eski erişim sonucu bayatlar */
  byId('sw-r').value = '260'; for (const fn of byId('sw-r').listeners.input) fn();
  assert.ok(byId('sw-reach-lines').textContent.includes('yeniden hesapla'));

  /* 3c) erişim hesaplanmadan kayıt: söylenir */
  byId('sw-name').value = 'Kubbe2'; byId('sw-cx').value = '300'; byId('sw-cy').value = '0'; byId('sw-cz').value = '400'; byId('sw-r').value = '200';
  for (const id of ['sw-name', 'sw-r']) for (const fn of byId(id).listeners.input) fn();
  await byId('sw-save').click();
  assert.ok(byId('sw-msg').textContent.includes('hesaplanmadan'), byId('sw-msg').textContent);

  /* 3d) erişilemeyen küre: ilk tık yazmaz, ikinci "Yine de kaydet" yazar */
  const unreach = { ok: true, reachable: false, elevation_range_deg: null, summary: 's', rows: [], grid: { azimuth_deg: [0], elevation_deg: [0], cells: ['0'] }, note: '' };
  await mountWith(() => live, async (p, m, b) => (p === '/api/frames/reach' ? unreach : ext(p, m, b)));
  byId('sw-name').value = 'Uzak'; byId('sw-cx').value = '2000'; byId('sw-cy').value = '0'; byId('sw-cz').value = '300'; byId('sw-r').value = '300';
  await byId('sw-reach').click();
  assert.ok(byId('sw-reach-lines').textContent.includes('yok'));
  await byId('sw-save').click();
  assert.strictEqual(puts().length, 0, 'erişilemeyen küre ilk tıkta yazılmamalı');
  assert.strictEqual(byId('sw-save').textContent, 'Yine de kaydet');
  await byId('sw-save').click();
  assert.strictEqual(puts().length, 1);

  /* 3e) robot hatası olduğu gibi gösterilir (400 yarıçap) */
  await mountWith(() => live, async (p, m) => {
    if (p === '/api/frames/reach') throw new ApiError(400, { message: 'yarıçap [0.05, 2] m aralığında olmalı, bulunan: 0.01' });
  });
  byId('sw-name').value = 'Kucuk'; byId('sw-cx').value = '300'; byId('sw-cy').value = '0'; byId('sw-cz').value = '300'; byId('sw-r').value = '10';
  await byId('sw-reach').click();
  assert.ok(byId('sw-msg').textContent.includes('yarıçap [0.05, 2]'), byId('sw-msg').textContent);

  /* 4) düzlem sihirbazı yalnız `frames` yazar (spheres anahtarı YOK = robot küreleri korur) */
  live = docOf({ frames: [userFrame('Tezgah')], spheres: [sphereDoc] });
  await mountWith(() => live, ext);
  byId('fw-name').value = 'Yeni';
  const at = [[0.3, 0, 0.1], [0.4, 0, 0.1], [0.3, 0.1, 0.1]];
  for (const [i, k] of ['origin', 'x', 'xy'].entries()) { tcpPos = at[i]; await byId(`fw-rec-${k}`).click(); }
  await byId('fw-save').click();
  assert.strictEqual(puts().length, 1, byId('fw-msg').textContent);
  assert.deepStrictEqual(Object.keys(puts()[0].b), ['frames']);
  assert.deepStrictEqual(puts()[0].b.frames.map((x) => x.name), ['Tezgah', 'Yeni']);
  assert.deepStrictEqual(live.spheres.map((s) => s.name), ['Eski']);

  /* 5) BİLİNMEYEN tür: sessizce atlanmaz, net hata, HİÇBİR ŞEY yazılmaz (iki sihirbaz da) */
  live = docOf({ frames: [userFrame('Tezgah'), { type: 'cylinder', name: 'Silindir' }], spheres: [sphereDoc] });
  await mountWith(() => live, ext);
  assert.ok(byId('fw-msg').textContent.includes('cylinder') && byId('fw-msg').textContent.includes('Sessizce atlanmaz'));
  assert.strictEqual(byId('fw-save').disabled, true);
  byId('sw-name').value = 'X1'; byId('sw-cx').value = '0'; byId('sw-cy').value = '0'; byId('sw-cz').value = '300'; byId('sw-r').value = '200';
  assert.strictEqual(byId('sw-save').disabled, true);
  await byId('sw-save').click();                                    // disabled: tıklama ulaşmaz
  byId('sw-save').disabled = false; await byId('sw-save').click();  // kilit atlansa bile kayıtta yine denetlenir
  assert.strictEqual(puts().length, 0, 'bilinmeyen tür varken hiçbir şey yazılmamalı');
  assert.ok(byId('sw-msg').textContent.includes('cylinder'));
  live = docOf({ frames: [userFrame('Tezgah')], spheres: [{ type: 'cone', name: 'Koni', center: [0, 0, 0.3], radius: 0.3 }, sphereDoc] });
  await mountWith(() => live, ext);
  assert.ok(byId('fw-msg').textContent.includes('cone'));
  assert.strictEqual(puts().length, 0);

  console.log('SPHERE_WIZARD_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
