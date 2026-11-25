/* Parçalar sayfası: GERÇEK pages/parts.js küçük SAHTE DOM + sahte HubScene/api üzerinde (jsdom yok). Sahne API'si §9-C
 * sözleşmesindeki gibi sonuç nesneleri döner ({ok,...}) ve gerçek SceneLogic.validateSpec ile doğrular: sayfanın
 * ürettiği her nesne tanımı (parts: önekli) geçerli olmak zorunda.
 *   node parts_page_check.js <web dizini>      pytest çağırır (tests/test_parts_page.py). */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);

class N {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.className = ''; this.value = ''; this.disabled = false;
    this.hidden = false; this._text = ''; this.files = null; this.style = { setProperty() {} };
    const self = this;
    this.classList = { toggle(c, on) { const s = new Set(self.className.split(' ').filter(Boolean)); (on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c); self.className = [...s].join(' '); } };
  }
  append(...c) { for (const x of c) this.children.push(typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x); }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : (k === 'href' ? this.href || null : null); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  async fire(t) { for (const f of this.listeners[t] || []) await f({ target: this }); }
  async click() { if (this.disabled) return; await this.fire('click'); }
}
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach((c) => all(c, f, out)); return out; };
const act = (root, a) => { const r = all(root, (n) => n.attrs['data-act'] === a); assert.ok(r.length, `düğme yok: ${a}`); return r[0]; };
const fld = (root, f) => { const r = all(root, (n) => n.attrs['data-f'] === f); assert.ok(r.length, `alan yok: ${f}`); return r[0]; };
const text = (n) => n.textContent;

global.window = global;
const head = new N('head');
global.document = { head, createElement: (t) => new N(t), getElementsByTagName: (t) => all(head, (n) => n.tagName === t) };
global.el = (tag, props = {}, children = []) => {                              // app.js'teki el() ile AYNI
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
    else if (k === 'style') for (const [sk, sv] of Object.entries(v)) e.style.setProperty(sk, sv);
    else if (k.startsWith('data-')) e.setAttribute(k, v); else e[k] = v;
  }
  for (const c of children) e.append(c);
  return e;
};
class ApiError extends Error { constructor(status, body) { super((body && body.message) || `HTTP ${status}`); this.status = status; this.body = body || {}; } }
global.S = { snap: { estop: { latched: false }, me: { can_control: true },
  robot: { mode: 'real', stale: false, tcp: { pos_m: [0.3, 0.0, 0.2], rpy_deg: [180, 0, 0] } } } };
require(path.join(WEB, 'shell.js'));                                           // modeInfo: yönelim varsayılanı moda göre
require(path.join(WEB, 'scene_logic.js'));
require(path.join(WEB, 'approach.js'));
const SL = SceneLogic;

/* ---------- sahte sunucu ---------- */
const srv = { parts: [], calls: [], seq: 0, checkReply: null, moveReply: null };
const unitM = { mm: 0.001, in: 0.0254, m: 1 };
const view = (p) => ({ ...p, url: `/api/parts/${p.id}/mesh`, unit_m: unitM[p.unit], triangles: 12, size_m: [0.04, 0.04, 0.04], size_warning: null });
global.api = async (p, method = 'GET', body) => {
  srv.calls.push({ p, method, body });
  if (p === '/api/parts' && method === 'GET') return { ok: true, parts: srv.parts.map(view), problems: [], limits: { max_parts: 20 },
    step: { available: false, message: 'STEP/IGES için dönüştürücü kurulu değil: CAD programında STL’e çevirip yükle.' } };
  if (p === '/api/parts' && method === 'POST') {
    const part = { id: (++srv.seq).toString(16).padStart(32, '0'), name: body.name || body.filename.replace(/\.[^.]+$/, ''), kind: body.kind, unit: body.unit,
      scale: 1, visible: true, pose: { pos_m: [0, 0, 0], rpy_deg: [0, 0, 0] }, tool_offset: body.kind === 'tool' ? { pos_m: [0, 0, 0], rpy_deg: [0, 0, 0] } : null };
    srv.parts.push(part); return { ok: true, part: view(part), warnings: [] };
  }
  let m = /^\/api\/parts\/([0-9a-f]{32})$/.exec(p);
  if (m && method === 'PUT') {
    const part = srv.parts.find((x) => x.id === m[1]); Object.assign(part, body);
    if (body.kind === 'tool' && !part.tool_offset) part.tool_offset = { pos_m: [0, 0, 0.05], rpy_deg: [0, 0, 0] };
    if (body.kind && body.kind !== 'tool') part.tool_offset = null;
    return { ok: true, part: view(part) };
  }
  if (m && method === 'DELETE') { srv.parts = srv.parts.filter((x) => x.id !== m[1]); return { ok: true }; }
  if (p === '/api/move/check') return srv.checkReply(body);
  if (p === '/api/move') return srv.moveReply(body);
  throw new ApiError(404, { error: 'not_found', message: 'yok' });
};
const callsTo = (p, method) => srv.calls.filter((c) => c.p === p && (!method || c.method === method));

/* ---------- sahte HubScene ---------- */
const ok = (extra = {}) => ({ ok: true, ...extra });
const scene = { implemented: true, objs: new Map(), log: [], pickCbs: new Set(), gizmoCbs: new Set(), gizmo: null, pickLayers: null, selected: null,
  list: () => [...scene.objs.keys()],
  info: (id) => (scene.objs.has(id) ? { layer: scene.objs.get(id).spec.layer, attach: scene.objs.get(id).spec.attach } : null),
  addObject(spec) {
    let s; try { s = SL.validateSpec(spec); } catch (e) { return { ok: false, error: 'bad_request', message: e.message }; }
    assert.ok(!scene.objs.has(s.id), `nesne zaten var: ${s.id}`); scene.objs.set(s.id, { spec: s, visible: true }); return ok({ id: s.id });
  },
  ready: async () => ok(),
  removeObject(id) { assert.ok(scene.objs.has(id), `yok: ${id}`); scene.objs.delete(id); return ok(); },
  setPose(id, pose) { scene.objs.get(id).spec.pose = SL.validateSpec({ ...scene.objs.get(id).spec, pose }).pose; return ok(); },
  setVisible(id, on) { scene.objs.get(id).visible = !!on; return ok(); },
  updateObject(id, patch) {
    scene.log.push(['update', id, patch]); const o = scene.objs.get(id).spec;
    if (patch.color !== undefined) o.color = patch.color; if (patch.scale !== undefined) o.geometry.scale = patch.scale; return ok();
  },
  select(id) { scene.selected = id; return ok(); }, focus() { return ok(); }, cameraPreset() { return ok(); },
  tcpPose: () => { throw new Error('sayfa modelden TCP hesaplamamalı: robotun bildirdiği TCP kullanılır'); },
  tcpOffset: () => ({ pos_m: [0, 0, 0.1], rpy_deg: [0, 0, 0] }),
  enableGizmo(id, o) { scene.gizmo = { id, mode: o.mode }; return ok(); }, disableGizmo() { scene.gizmo = null; return ok(); },
  setPickLayers(l) { scene.pickLayers = l; },
  onPick(cb) { scene.pickCbs.add(cb); return () => scene.pickCbs.delete(cb); },
  onGizmoChange(cb) { scene.gizmoCbs.add(cb); return () => scene.gizmoCbs.delete(cb); } };
let def = null;
let mounts = 0;
global.HubPages = { register(d) { def = d; }, async mountView() { mounts += 1; }, async scene() { return scene; } };
require(path.join(WEB, 'pages', 'parts.js'));

const idOf = (i) => srv.parts[i].id;
const pick = (objId, point, normal, layer = 'workpiece') => { for (const cb of [...scene.pickCbs]) cb({ objectId: objId, layer, point_m: point, normal }); };
const tick = () => new Promise((r) => setImmediate(r));

(async () => {
  assert.ok(def && def.id === 'parts' && def.scripts.includes('/scene_logic.js') && def.scripts.includes('/approach.js') && typeof def.order === 'number');
  assert.deepStrictEqual(def.styles, ['/pages/parts.css']);                // §5: yalnız kendi CSS'i, HubPages yükler
  const root = new N('section');
  await def.mount(root);
  def.onShow(); await tick(); assert.strictEqual(mounts, 1, 'mount sonrası ilk onShow ikinci yükleme yapmamalı');
  assert.strictEqual(scene.pickCbs.size, 1); assert.deepStrictEqual(scene.pickLayers, ['workpiece', 'environment']);
  assert.ok(!all(root, (n) => n.attrs['data-id']).length, 'liste başta boş olmalı');

  /* --- yükleme: gövde, base64, sınırlar, STEP --- */
  const bytes = new Uint8Array([1, 2, 3, 250, 251, 0, 7]);
  const up = fld(root, 'file'), goBtn = act(root, 'upload');
  up.files = [{ name: 'kare.stl', size: bytes.length, arrayBuffer: async () => bytes.buffer }];
  fld(root, 'newunit').value = 'in'; fld(root, 'newkind').value = 'workpiece';
  await up.fire('change'); await goBtn.click();
  let post = callsTo('/api/parts', 'POST'); assert.strictEqual(post.length, 1);
  assert.deepStrictEqual(post[0].body, { filename: 'kare.stl', data_b64: Buffer.from(bytes).toString('base64'), kind: 'workpiece', unit: 'in' });
  assert.ok(scene.objs.has(`parts:${idOf(0)}`) && scene.objs.get(`parts:${idOf(0)}`).spec.layer === 'workpiece');
  assert.strictEqual(scene.objs.get(`parts:${idOf(0)}`).spec.geometry.scale, 0.0254, 'birim ölçeğe girmeli');
  assert.strictEqual(scene.selected, `parts:${idOf(0)}`);
  assert.match(text(root), /kare/); assert.match(text(root), /12 üçgen/); assert.match(text(root), /1 \/ 20 parça/);

  up.files = [{ name: 'kasa.STEP', size: 10, arrayBuffer: async () => bytes.buffer }];
  await up.fire('change');
  assert.ok(goBtn.disabled, 'STEP için yükle düğmesi kapalı olmalı'); assert.match(text(root), /dönüştürücü kurulu değil/);
  await goBtn.click(); assert.strictEqual(callsTo('/api/parts', 'POST').length, 1, 'STEP sunucuya gitmemeli');
  up.files = [{ name: 'dev.stl', size: 51 * 1024 * 1024, arrayBuffer: async () => { throw new Error('okunmamalı'); } }];
  await up.fire('change'); await goBtn.click();
  assert.strictEqual(callsTo('/api/parts', 'POST').length, 1); assert.match(text(root), /50 MB sınırını aşıyor/);

  /* --- ad, sayı alanları, ölçek, görünürlük --- */
  const pid = idOf(0), sid = `parts:${pid}`;
  fld(root, 'x').value = '12,5'; fld(root, 'z').value = '30'; fld(root, 'p').value = '45';
  await fld(root, 'x').fire('change');
  let put = callsTo(`/api/parts/${pid}`, 'PUT'); assert.strictEqual(put.length, 1);
  assert.deepStrictEqual(put[0].body.pose, { pos_m: [0.0125, 0, 0.03], rpy_deg: [0, 45, 0] });
  assert.deepStrictEqual(scene.objs.get(sid).spec.pose, put[0].body.pose);
  fld(root, 'y').value = 'abc'; await fld(root, 'y').fire('change');
  assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').length, 1, 'geçersiz alan sunucuya gitmemeli'); assert.match(text(root), /sayı olmalı/);
  fld(root, 'y').value = '0'; fld(root, 'scale').value = '5000'; await fld(root, 'scale').fire('change');
  assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').length, 1); assert.match(text(root), /Ölçek/);
  fld(root, 'scale').value = '2'; await fld(root, 'scale').fire('change');
  assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body.scale, 2); assert.strictEqual(scene.objs.get(sid).spec.geometry.scale, 0.0254 * 2);
  fld(root, 'name').value = 'Plaka'; await fld(root, 'name').fire('change');
  assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body.name, 'Plaka');
  await act(root, 'vis').click(); assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body.visible, false); assert.strictEqual(scene.objs.get(sid).visible, false);
  await act(root, 'vis').click(); assert.strictEqual(scene.objs.get(sid).visible, true);

  /* --- yarış: kayıt uçuştayken başka alana yazmak girdiyi ezmemeli ---
   * Gerçek hata (tarayıcıda yakalandı): x'in kaydının eski yanıtı gelince alanlar sunucunun eski durumuyla dolduruldu;
   * kullanıcının yazdığı y=-30 silindi ve sonraki `change` y=0'ı kaydetti. */
  {
    const realApi = global.api, held = []; let hold = true;
    global.api = (p, method, body) => (hold && method === 'PUT'
      ? new Promise((res, rej) => held.push(() => realApi(p, method, body).then(res, rej))) : realApi(p, method, body));
    const release = async () => { held.shift()(); await tick(); await tick(); };
    fld(root, 'x').value = '164'; await fld(root, 'x').fire('input'); await fld(root, 'x').fire('change');   // kayıt 1 uçuşta
    assert.strictEqual(held.length, 1);
    fld(root, 'y').value = '-30'; await fld(root, 'y').fire('input'); await fld(root, 'y').fire('change');  // kayıt 2: sıraya girer
    fld(root, 'z').value = '79'; await fld(root, 'z').fire('input');
    assert.strictEqual(held.length, 1, 'kayıtlar sırayla gitmeli: ikincisi birincinin yanıtını beklemeli');
    await release();                                                      // kayıt 1'in yanıtı (eski y) şimdi geldi
    assert.strictEqual(fld(root, 'y').value, '-30', 'bayat yanıt yazılan girdiyi ezmemeli');
    assert.strictEqual(fld(root, 'z').value, '79', 'bayat yanıt yazılan girdiyi ezmemeli');
    assert.strictEqual(held.length, 1); await release();                  // kayıt 2
    await fld(root, 'z').fire('change'); await release();                 // kayıt 3: z
    hold = false; global.api = realApi;
    assert.deepStrictEqual(srv.parts.find((x) => x.id === pid).pose.pos_m, [0.164, -0.03, 0.079], 'üç alanın üçü de sunucuya ulaşmalı');
    assert.strictEqual(fld(root, 'y').value, '-30');
  }

  /* --- TCP'ye yerleştir (robotun BİLDİRDİĞİ TCP) + gizmo --- */
  S.snap.robot.stale = true; def.onState(S.snap);
  const putsBefore = callsTo(`/api/parts/${pid}`, 'PUT').length;
  await act(root, 'tcp').click(); assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').length, putsBefore); assert.match(text(root), /Robot verisi bayat/);
  S.snap.robot.stale = false; def.onState(S.snap);
  await act(root, 'tcp').click();
  assert.match(text(root), /BİLDİRDİĞİ TCP/);
  assert.deepStrictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body.pose, { pos_m: [0.3, 0, 0.2], rpy_deg: [180, 0, 0] });
  await act(root, 'gizmo-translate').click(); assert.deepStrictEqual(scene.gizmo, { id: sid, mode: 'translate' });
  const before = callsTo(`/api/parts/${pid}`, 'PUT').length;
  for (const cb of scene.gizmoCbs) cb({ id: sid, pose: { pos_m: [0.1, 0.2, 0.3], rpy_deg: [0, 0, 0] }, phase: 'move' });
  assert.strictEqual(callsTo(`/api/parts/${pid}`, 'PUT').length, before, 'sürüklerken kayıt yok'); assert.strictEqual(fld(root, 'x').value, '100');
  for (const cb of scene.gizmoCbs) cb({ id: sid, pose: { pos_m: [0.1, 0.2, 0.3], rpy_deg: [0, 0, 0] }, phase: 'end' });
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body.pose.pos_m, [0.1, 0.2, 0.3]);
  await act(root, 'gizmo-off').click(); assert.strictEqual(scene.gizmo, null);

  /* --- takıma çevir: flanşa bağlanır, ofset alanları --- */
  fld(root, 'kind').value = 'tool'; await fld(root, 'kind').fire('change');
  assert.deepStrictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body, { kind: 'tool' });
  assert.strictEqual(scene.objs.get(sid).spec.layer, 'tool'); assert.strictEqual(scene.objs.get(sid).spec.attach, 'flange');
  assert.match(text(root), /robota uygulanmaz/); assert.ok(act(root, 'tcp').hidden, 'takımda TCP’ye yerleştir yok');
  fld(root, 'z').value = '55'; await fld(root, 'z').fire('change');
  assert.deepStrictEqual(callsTo(`/api/parts/${pid}`, 'PUT').pop().body.tool_offset.pos_m, [0, 0, 0.055]);

  /* --- ikinci parça (iş parçası): dokun-git --- */
  up.files = [{ name: 'tabla.stl', size: 5, arrayBuffer: async () => bytes.buffer }]; fld(root, 'newunit').value = 'mm';
  await up.fire('change'); await goBtn.click();
  const wid = idOf(1), wsid = `parts:${wid}`;
  const okReply = () => ({ ok: true, violations: [] });
  srv.checkReply = () => okReply(); srv.moveReply = () => ({ ok: true, job_id: 'j7' });
  const near = act(root, 'approach'), touch = act(root, 'touch'), confirm = act(root, 'confirm');
  assert.ok(near.disabled && touch.disabled, 'nokta seçilmeden hedef kurulamaz');

  pick(wsid, [0.3, 0.0, 0.1], [0, 0, 1]);
  assert.ok(scene.objs.has('parts:ap-point')); assert.ok(!near.disabled); assert.strictEqual(scene.selected, wsid);
  await near.click();                                                      // d = 50 mm (varsayılan), hız %10
  let chk = callsTo('/api/move/check'); assert.strictEqual(chk.length, 1);
  const b = chk[0].body; assert.strictEqual(b.type, 'linear'); assert.strictEqual(b.speed_pct, 10);
  assert.strictEqual(fld(root, 'orientmode').value, 'fixed', 'mode=real: varsayılan yönelim Sabit'); assert.strictEqual(b.orientation, 'fixed');
  b.pos_m.forEach((v, i) => assert.ok(Math.abs(v - [0.3, 0, 0.15][i]) < 1e-12, `hedef konumu: ${b.pos_m}`));
  const Rm = SL.rpyToMatrix(b.rpy_deg); [Rm[2], Rm[5], Rm[8]].forEach((v, i) => assert.ok(Math.abs(v - [0, 0, -1][i]) < 1e-9, 'takım z ekseni −normal olmalı'));
  assert.ok(!confirm.hidden, 'kontrol geçti: onay düğmesi görünmeli'); assert.strictEqual(callsTo('/api/move').length, 0, 'onaydan önce hareket yok');
  assert.ok(scene.objs.has('parts:ap-frame') && scene.objs.has('parts:ap-line'));
  assert.ok(scene.objs.has('parts:ap-ghost'), 'görünür takım varken gölge takım çizilmeli');
  await confirm.click();
  let mv = callsTo('/api/move'); assert.strictEqual(mv.length, 1); assert.deepStrictEqual(mv[0].body, b); assert.match(text(root), /iş j7/);
  assert.ok(confirm.hidden); await confirm.click(); assert.strictEqual(callsTo('/api/move').length, 1, 'plan tek kullanımlık');

  await touch.click();                                                     // Git: d = 0 → hedef = dokunulan nokta
  chk = callsTo('/api/move/check'); chk[chk.length - 1].body.pos_m.forEach((v, i) => assert.ok(Math.abs(v - [0.3, 0, 0.1][i]) < 1e-12));

  /* ulaşılamaz / güvenli kutu dışı: kırmızı + neden + HAREKET YOK */
  srv.checkReply = () => ({ ok: false, violations: [{ kind: 'outside_safe_zone', message: 'Hedef güvenli kutunun dışında' }] });
  const moves0 = callsTo('/api/move').length;
  await near.click();
  assert.ok(confirm.hidden); assert.match(text(root), /güvenli kutunun dışında/); assert.match(text(root), /GÖNDERİLMEDİ/);
  assert.ok(scene.log.some((l) => l[1] === 'parts:ap-line' && l[2].color === 0xda3633), 'çizgi kırmızıya dönmeli');
  await confirm.click(); assert.strictEqual(callsTo('/api/move').length, moves0, 'kontrolden geçmeyen hedefe hareket gitmemeli');
  srv.checkReply = () => { throw new ApiError(404, { error: 'not_found' }); };
  await near.click(); assert.match(text(root), /hareket GÖNDERİLMEDİ/); assert.ok(confirm.hidden);

  /* kontrol geçti, sonra ayar değişti → plan düşer */
  srv.checkReply = okReply; await near.click(); assert.ok(!confirm.hidden);
  fld(root, 'd').value = '80'; await fld(root, 'd').fire('input');
  assert.ok(confirm.hidden, 'ayar değişince plan düşmeli'); await confirm.click(); assert.strictEqual(callsTo('/api/move').length, moves0);
  fld(root, 'd').value = '-5'; await near.click(); assert.match(text(root), /Mesafe 0–/); fld(root, 'd').value = '50';

  /* ACİL DUR: düğmeler kapanır, plan düşer, komut GİTMEZ (423 de ele alınır) */
  await near.click(); assert.ok(!confirm.hidden);
  S.snap.estop.latched = true; def.onState(S.snap);
  assert.ok(near.disabled && touch.disabled && confirm.hidden, 'ACİL DUR’da hareket düğmeleri kapalı'); assert.match(text(root), /ACİL DUR aktif/);
  const nCheck = callsTo('/api/move/check').length; await near.click(); await confirm.click();
  assert.strictEqual(callsTo('/api/move/check').length, nCheck); assert.strictEqual(callsTo('/api/move').length, moves0);
  S.snap.estop.latched = false; def.onState(S.snap);
  srv.checkReply = () => { throw new ApiError(423, { error: 'estop_latched', message: 'ACİL DUR' }); };
  await near.click(); assert.match(text(root), /ACİL DUR aktif: önce kilidi aç/); assert.strictEqual(callsTo('/api/move').length, moves0);
  S.snap.me.can_control = false; def.onState(S.snap); assert.ok(near.disabled, 'salt-okunur oturumda hareket yok'); S.snap.me.can_control = true; def.onState(S.snap);

  /* bayat robot verisi: düğmeler kapanır, plan düşer, komut GİTMEZ */
  srv.checkReply = okReply; await near.click(); assert.ok(!confirm.hidden);
  S.snap.robot.stale = true; def.onState(S.snap);
  assert.ok(near.disabled && touch.disabled && confirm.hidden, 'bayat veride hareket düğmeleri kapalı'); assert.match(text(root), /Robot verisi bayat/);
  const nMove = callsTo('/api/move').length; await confirm.click(); assert.strictEqual(callsTo('/api/move').length, nMove);
  S.snap.robot.stale = false; def.onState(S.snap);

  /* --- yönelim kipi (UI_CONTRACT §9-B): SİM'de varsayılan Serbest, gerçek/tezgâhta Sabit; `orientation` HER çağrıda gider --- */
  {
    const mode = fld(root, 'orientmode'), orient = fld(root, 'orient');
    const orientField = all(root, (n) => n.tagName === 'label' && n.children.includes(orient))[0];
    const freeObjs = () => [scene.objs.has('parts:ap-tgt'), scene.objs.has('parts:ap-frame'), scene.objs.has('parts:ap-ghost')];
    srv.checkReply = okReply; srv.moveReply = () => ({ ok: true, job_id: 'jm' });
    assert.deepStrictEqual([...mode.children.map((o) => o.value)], ['free', 'fixed']);
    assert.strictEqual(mode.value, 'fixed'); assert.ok(!orientField.hidden, 'Sabit: takım yönelimi alanı görünür');

    S.snap.robot.mode = 'sim'; def.onState(S.snap);                          // mod SİM'e döndü: varsayılan Serbest
    assert.strictEqual(mode.value, 'free'); assert.ok(orientField.hidden, 'Serbest: takım yönelimi alanı gizli');
    assert.match(text(root), /yönelimi robot seçer/); assert.ok(!/GERÇEK ya da bilinmeyen/.test(text(root)));
    const c0 = callsTo('/api/move/check').length, m0 = callsTo('/api/move').length;
    await near.click();
    let cb = callsTo('/api/move/check')[c0].body;
    assert.deepStrictEqual(Object.keys(cb).sort(), ['orientation', 'pos_m', 'speed_pct', 'type'], 'serbest gövde rpy_deg taşımamalı');
    assert.strictEqual(cb.orientation, 'free'); assert.ok(!('rpy_deg' in cb));
    cb.pos_m.forEach((v, i) => assert.ok(Math.abs(v - [0.3, 0, 0.15][i]) < 1e-12, `serbest hedef konumu: ${cb.pos_m}`));
    assert.deepStrictEqual(freeObjs(), [true, false, false], 'serbest: yalnız hedef noktası; çerçeve/gölge takım yok');
    assert.match(text(root), /yönelimi robot seçer\)\. Onaylarsan/); assert.ok(!confirm.hidden);
    await confirm.click();
    assert.deepStrictEqual(callsTo('/api/move')[m0].body, cb, 'onaylanan hareket kontrol edilenle AYNI gövde'); assert.ok(!('rpy_deg' in callsTo('/api/move')[m0].body));

    await near.click(); assert.ok(!confirm.hidden);                          // kipi elle Sabit yap: plan düşer, hedef işareti kalkar
    mode.value = 'fixed'; await mode.fire('input');
    assert.ok(confirm.hidden, 'kip değişince plan düşmeli'); assert.deepStrictEqual(freeObjs(), [false, false, false]);
    assert.ok(!orientField.hidden); assert.match(text(root), /Sabit: alet yönelimi/);
    await confirm.click(); assert.strictEqual(callsTo('/api/move').length, m0 + 1, 'kip değişince eski plan gitmemeli');
    await near.click(); cb = callsTo('/api/move/check').pop().body;
    assert.strictEqual(cb.orientation, 'fixed'); assert.ok(Array.isArray(cb.rpy_deg) && cb.rpy_deg.length === 3);
    assert.deepStrictEqual(freeObjs(), [false, true, true], 'sabit: çerçeve + gölge takım, hedef noktası yok');
    def.onState(S.snap); assert.strictEqual(mode.value, 'fixed', 'aynı modda durum yayını elle seçimi ezmemeli'); assert.ok(!confirm.hidden, 'plan korunmalı');

    mode.value = 'free'; await mode.fire('input');                           // gerçek robota geçiş: elle seçim bile Sabit'e döner
    S.snap.robot.mode = 'real'; await near.click(); assert.ok(!confirm.hidden);
    def.onState(S.snap);
    assert.strictEqual(mode.value, 'fixed', 'mod değişince varsayılan (gerçek: Sabit) yeniden uygulanmalı'); assert.ok(confirm.hidden, 'mod değişince plan düşmeli');
    mode.value = 'free'; await mode.fire('input');                           // gerçek robotta bilerek Serbest: uyarı görünür
    assert.match(text(root), /GERÇEK ya da bilinmeyen robotta/);
    assert.ok(all(root, (n) => /GERÇEK ya da bilinmeyen/.test(n._text) && /warn/.test(n.className)).length, 'uyarı satırı warn sınıfında olmalı');
    def.onState(S.snap); assert.strictEqual(mode.value, 'free', 'elle seçim aynı modda korunur');
    mode.value = 'fixed'; await mode.fire('input');

    const robot = S.snap.robot;                                             // bilinmeyen mod: seçime DOKUNULMAZ (yayın aksaması varsayılanı oynatmasın)
    mode.value = 'free'; await mode.fire('input'); S.snap.robot = null; def.onState(S.snap);
    assert.strictEqual(mode.value, 'free'); S.snap.robot = robot; mode.value = 'fixed'; await mode.fire('input');
    S.snap.robot.mode = 'sim'; def.onState(S.snap); assert.strictEqual(mode.value, 'free');
    S.snap.bench = { open: true }; def.onState(S.snap); assert.strictEqual(mode.value, 'fixed', 'tezgâh: Sabit'); delete S.snap.bench;
    S.snap.robot.mode = 'real'; def.onState(S.snap); assert.strictEqual(mode.value, 'fixed');
    /* serbest kipte de ACİL DUR: düğmeler kapanır, komut GİTMEZ */
    S.snap.robot.mode = 'sim'; def.onState(S.snap); await near.click(); assert.ok(!confirm.hidden);
    S.snap.estop.latched = true; def.onState(S.snap);
    const mN = callsTo('/api/move').length; await confirm.click(); assert.strictEqual(callsTo('/api/move').length, mN); assert.ok(confirm.hidden);
    S.snap.estop.latched = false; S.snap.robot.mode = 'real'; def.onState(S.snap); assert.strictEqual(mode.value, 'fixed');
  }

  /* §9-C: 3B gerçeklenmemişse (implemented:false) sayfa liste yöneticisi olarak çalışır, hareket düğmeleri kapalı kalır */

  /* --- sayfadan çıkış: gizmo, plan, işaretler, dinleyiciler --- */
  srv.checkReply = okReply; await near.click(); assert.ok(!confirm.hidden);
  await act(root, 'gizmo-translate').click(); assert.ok(scene.gizmo);
  def.onHide();
  assert.strictEqual(scene.gizmo, null); assert.strictEqual(scene.pickCbs.size, 0); assert.strictEqual(scene.gizmoCbs.size, 0);
  assert.ok(confirm.hidden); assert.ok(!scene.objs.has('parts:ap-point') && !scene.objs.has('parts:ap-frame') && !scene.objs.has('parts:ap-ghost'));
  assert.deepStrictEqual(scene.pickLayers, ['workpiece', 'tool', 'environment', 'markers']); assert.strictEqual(scene.selected, null);
  def.onShow(); await tick(); await tick(); assert.strictEqual(mounts, 2, 'onHide sonrası onShow tuvali yeniden alır');
  assert.strictEqual(scene.pickCbs.size, 1); assert.deepStrictEqual(scene.pickLayers, ['workpiece', 'environment']);

  /* --- silme: iki adım --- */
  // seçili parça: tabla
  const del = act(root, 'delete'); await del.click();
  assert.strictEqual(callsTo(`/api/parts/${wid}`, 'DELETE').length, 0, 'ilk dokunuş silmez'); assert.match(text(del), /Emin misin/);
  await del.click(); await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  assert.strictEqual(callsTo(`/api/parts/${wid}`, 'DELETE').length, 1); assert.ok(!scene.objs.has(wsid)); assert.strictEqual(srv.parts.length, 1);
  console.log('PARTS_PAGE_JS_OK');
  process.exit(0);                                                         // bekleyen zamanlayıcılar süreci tutmasın
})().catch((e) => { console.error(e); process.exit(1); });
