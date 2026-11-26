/* P2 Program sayfası: sahte DOM ile sayfa davranışı (tarayıcı gerekmez). Saf mantık (R12): program_logic_check.js.
 *   node program_page_check.js <web dizini>
 * Sınananlar: mantık betiğinin sayfa açılmadan yüklenmesi (R12), Çalıştır kapısı (önizleme zorunlu, düzenleme önizlemeyi
 * geçersiz kılar, ihlal/ACİL DUR/boş), satır renkleri, çalışırken düzenleme kilidi, gerçek mod onayı, 409 busy,
 * kalp atışı, sayfa gizlenince duraklatma ve (varsa) HubScene işaret/yol/onPick. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(process.argv[2]);
const PAGE = path.join(WEB, 'pages', 'program.js'), LOGIC = path.join(WEB, 'program_logic.js');

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
  off() { for (let n = this; n; n = n.parent) if (n.disabled) return true; return false; }   // <fieldset disabled> altındakiler de kapalı
  fire(ev) { if (this.off()) return; (this.listeners[ev] || []).forEach((fn) => fn({ target: this })); }
  click() { this.fire('click'); }
  set(v, ev = 'change') { this.value = v; this.fire(ev); }
}
global.window = global;
global.el = (tag, props = {}, children = []) => {
  const e = new N(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'text') e.textContent = v; else if (k === 'class') e.className = v; else if (k.startsWith('data-')) e.dataset[k.slice(5)] = v; else e[k] = v; }
  e.append(...children); return e;
};
const walk = (n, fn) => { fn(n); n.children.forEach((c) => walk(c, fn)); };
const all = (root, pred) => { const out = []; walk(root, (n) => { if (pred(n)) out.push(n); }); return out; };
const btn = (root, text) => all(root, (n) => n.tagName === 'BUTTON' && n.textContent === text)[0];
const byTitle = (root, title) => all(root, (n) => n.title === title);
const flush = (ms = 0) => new Promise((r) => setTimeout(r, ms));

/* Gerçek HubScene gibi davranır: kimlik "<sayfa>:<ad>", tür başına geometri doğrulaması, fırlatmaz {ok:false} döner. */
const ID_RE = /^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,64}$/;
function fakeScene(calls, { implemented = true, reject = null } = {}) {
  const objs = new Map(), no = (error, message) => ({ ok: false, error, message });
  const H = { implemented, objs, pickLayers: null,
    addObject(o) {
      if (reject) return no('bad_request', reject);
      if (typeof o.id !== 'string' || !ID_RE.test(o.id)) return no('bad_request', 'id "<sayfa>:<ad>" olmalı');
      if (objs.has(o.id)) return no('exists', `nesne zaten var: ${o.id}`);
      const g = o.geometry || {};
      if (o.kind === 'marker' && !(g.radius_m > 0)) return no('bad_request', 'işaret yarıçapı');
      if (o.kind === 'frame' && g.size_m !== undefined && typeof g.size_m !== 'number') return no('bad_request', 'çerçeve boyu sayı olmalı');
      if (o.kind === 'polyline' && !(Array.isArray(g.points_m) && g.points_m.length >= 2)) return no('bad_request', 'poliçizgi 2+ nokta');
      objs.set(o.id, o); calls.push(['add', o]); return { ok: true, id: o.id };
    },
    removeObject(id) { if (!objs.has(id)) return no('not_found', id); objs.delete(id); calls.push(['rm', id]); return { ok: true }; },
    setPose(id, pose) { if (!objs.has(id)) return no('not_found', id); objs.get(id).pose = pose; calls.push(['pose', id, pose]); return { ok: true }; },
    enableGizmo(id, o) { if (!objs.has(id)) return no('not_found', id); calls.push(['gizmo', id, o]); return { ok: true }; },
    disableGizmo() { calls.push(['nogizmo']); return { ok: true }; },
    setPickLayers(l) { H.pickLayers = l; },
    onPick(cb) { H.pick = cb; return () => {}; }, onGizmoChange(cb) { H.gizmo = cb; return () => {}; } };
  return H;
}

/* Her senaryo kendi sayfa kopyasını (taze durum) yükler. */
function load(script, opts = {}) {
  const { scene = false } = opts;
  const calls = [], toasts = [], timers = [];
  const handlers = {
    'GET /api/program/state': () => ({ ok: true, runner: { state: 'idle', running: false }, estop: false,
      pose: { joints_deg: [0, 10, 20, 30, 40, 50], pos_m: [0.3, 0.1, 0.2], rpy_deg: [180, 0, 0], open_pct: 50 },
      backend: { label: 'SAHTE (mock)', measured: false, mock: true }, limits: {} }),
    'GET /api/program': () => ({ ok: true, programs: [] }),
    'POST /api/program/check_move': () => ({ ok: true, violations: [] }),
    ...script };
  global.api = async (path, method = 'GET', body) => {
    calls.push({ path, method, body });
    const h = handlers[`${method} ${path}`];
    if (!h) throw Object.assign(new Error(`beklenmeyen çağrı ${method} ${path}`), { status: 404, body: {} });
    return h(body);
  };
  global.toast = (msg, bad) => toasts.push({ msg, bad: !!bad });
  global.setInterval = (fn) => { timers.push(fn); return timers.length; };
  global.clearInterval = (id) => { timers[id - 1] = null; };
  global.document = { head: new N('head'), getElementsByTagName: (t) => all(global.document.head, (n) => n.tagName === t.toUpperCase()) };
  const sceneCalls = [];
  if (scene) global.HubScene = fakeScene(sceneCalls, scene === true ? {} : scene);
  else delete global.HubScene;
  let def = null;
  delete global.ProgramLogic;                  // her senaryoda mantık betiği TAZE yüklenir (sayfa kaydı bunu ister)
  const loads = [];
  global.HubPages = { register(d) { def = d; }, mountView: async () => {},
    loadScript: (src) => { loads.push(src); return opts.logicFails ? Promise.reject(new Error(`yüklenemedi: ${src}`))
      : Promise.resolve().then(() => { delete require.cache[LOGIC]; require(LOGIC); }); } };   // gerçek pages.js gibi: betik yüklenince window.<Ad> hazır
  new Function(fs.readFileSync(PAGE, 'utf8'))();
  return { def, calls, toasts, timers, sceneCalls, loads, H: global.HubScene };
}
const snap = (o = {}) => ({ estop: { latched: false }, robot: { mode: 'fake' }, me: { can_control: true }, ...o });
const PREVIEW_OK = { ok: true, violations: 0, rows: [{ line: 0, sub: null, kind: 'move_j', ok: true, violations: [] }] };

(async () => {
  /* ---- 1) kayıt: mantık betiği sayfa AÇILMADAN yüklenir; hazır olana dek ekleme nazikçe reddedilir ---- */
  const probe = load({});
  assert.strictEqual(probe.def.id, 'program');
  assert.deepStrictEqual(probe.loads, ['/program_logic.js'], 'sayfa betiği mantığı kendisi ister (Algoritmalar Program açılmadan ekleyebilir)');
  assert.deepStrictEqual(window.HubProgram.addSteps([{ kind: 'wait', s: 1 }]), { ok: false, message: 'Program mantığı yükleniyor: birkaç saniye sonra tekrar dene' });
  await flush();
  assert.ok(window.ProgramLogic, 'mantık yüklendi');
  assert.deepStrictEqual(window.HubProgram.addSteps([{ kind: 'wait', s: 1 }]), { ok: true, total: 1 }, 'yüklenince ekleme çalışır (sayfa açılmadan)');
  const broken = load({}, { logicFails: true });
  await assert.rejects(broken.def.mount(new N('section')), /program_logic\.js/, 'mantık yüklenemezse sayfa kendi kutusunda hata verir (mount reddeder)');
  assert.strictEqual(window.HubProgram.addSteps([{ kind: 'wait', s: 1 }]).ok, false, 'mantıksız ekleme sessizce kabul edilmez');

  /* ---- 2) sayfa: waypoint kaydet, adım ekle, ZORUNLU önizleme, düzenleme önizlemeyi bozar ---- */
  let previewReply = PREVIEW_OK;
  let slow = 0;
  const a = load({ 'POST /api/program/check': async () => { await flush(slow); return previewReply; } });
  const root = new N('section'); await a.def.mount(root); await flush(); a.def.onState(snap());
  assert.ok(!root.textContent.includes('undefined'));
  assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'boş programda Çalıştır kapalı');
  assert.match(root.textContent, /SAHTE \(mock\) hareket servisi: ölçüm değildir/);
  await btn(root, 'Şu anki pozu kaydet').click(); await flush();
  assert.strictEqual(byTitle(root, 'Waypoint adı')[0].value, 'Nokta 1');
  const adder = byTitle(root, 'Adım türü').pop(); adder.value = 'move_j'; btn(root, '+ Adım ekle').click();
  assert.strictEqual(byTitle(root, 'Waypoint').length, 1, 'adım waypoint\'e bağlı eklendi');
  assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'önizleme yokken Çalıştır kapalı');
  assert.match(root.textContent, /Önce "Önizle"/);
  btn(root, 'Önizle').click(); await flush();
  assert.strictEqual(btn(root, 'Çalıştır').disabled, false, 'önizleme geçti -> Çalıştır açık');
  assert.match(root.textContent, /✓ Önizleme geçti/);
  const speed = byTitle(root, 'Hız %')[0]; speed.value = '40'; speed.fire('input');
  assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'her düzenleme önizlemeyi geçersiz kılar');
  previewReply = { ok: false, violations: 1, rows: [{ line: 0, sub: null, kind: 'move_j', ok: false, violations: [{ kind: 'joint_limit', message: 'eklem 2 limit dışı' }] }] };
  btn(root, 'Önizle').click(); await flush();
  assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'ihlal varken Çalıştır kapalı');
  assert.ok(all(root, (n) => n.className.includes('pg-step') && n.className.includes('bad')).length >= 1, 'ihlalli satır kırmızı');
  assert.match(root.textContent, /eklem 2 limit dışı/);
  previewReply = PREVIEW_OK; slow = 40; btn(root, 'Önizle').click();
  speed.value = '55'; speed.fire('input');                              // yanıt beklenirken hız düzenlenir
  await flush(80); assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'beklenirken düzenlendi: eski önizleme geçerli SAYILMAZ');
  btn(root, 'Önizle').click(); byTitle(root, 'Adım türü').pop().value = 'wait'; btn(root, '+ Adım ekle').click();   // bu kez ADIM eklenir
  await flush(80); assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'adım eklenince eski önizleme geçersiz');
  const added = all(root, (n) => n.tagName === 'BUTTON' && n.textContent === '✕' && n.title === 'Sil'); added[added.length - 1].click();   // eklenen bekleme adımı silinir
  slow = 0; btn(root, 'Önizle').click(); await flush(20);
  assert.strictEqual(btn(root, 'Çalıştır').disabled, false);
  btn(root, '✕').click();                                               // ilk '✕' = waypoint silme: adım silinmiş waypoint'e bağlı kalır
  assert.strictEqual(btn(root, 'Çalıştır').disabled, true, 'silinmiş waypoint -> Çalıştır kapalı');
  assert.ok(all(root, (n) => n.className.includes('pg-step') && n.className.includes('bad')).length >= 1, 'silinmiş waypoint satırı kırmızı');
  assert.match(root.textContent, /waypoint yok/);

  /* ---- 3) çalıştırma: onay, kilit, 409, kalp atışı, duraklatma ---- */
  let runState = { state: 'idle', running: false }, runReply = () => ({ ok: true, run_id: 1 });
  const b = load({ 'POST /api/program/check': () => PREVIEW_OK, 'POST /api/program/run': (body) => runReply(body),
    'GET /api/program/state': () => ({ ok: true, runner: runState, estop: false, pose: { joints_deg: [1, 2, 3, 4, 5, 6] }, backend: { label: 'SAHTE (mock)', measured: false, mock: true } }),
    'POST /api/program/heartbeat': () => ({ ok: true }), 'POST /api/program/pause': () => ({ ok: true }) });
  const r2 = new N('section'); await b.def.mount(r2); await flush(); b.def.onState(snap({ robot: { mode: 'real' } }));
  await btn(r2, 'Şu anki pozu kaydet').click(); await flush();
  byTitle(r2, 'Adım türü').pop().value = 'move_j'; btn(r2, '+ Adım ekle').click();
  btn(r2, 'Önizle').click(); await flush();
  btn(r2, 'Çalıştır').click(); await flush();
  assert.ok(!b.calls.some((c) => c.path === '/api/program/run'), 'gerçek modda onaydan önce run GİTMEZ');
  const confirm = all(r2, (n) => n.className.includes('pg-confirm'))[0]; assert.strictEqual(confirm.hidden, false);
  btn(r2, 'Evet, çalıştır').click(); await flush();
  const run = b.calls.find((c) => c.path === '/api/program/run');
  assert.strictEqual(run.body.confirm_real, true); assert.strictEqual(run.body.mode, 'run'); assert.strictEqual(run.body.program.steps.length, 1);
  runState = { state: 'running', running: true, line: 0, sub: null, done: 0, total: 1, say: 'eklem hareketi' };
  await b.timers.find(Boolean)(); await flush();                        // yoklama: durum + kalp atışı
  assert.ok(b.calls.some((c) => c.path === '/api/program/heartbeat'), 'çalışırken kalp atışı gider');
  const fsets = all(r2, (n) => n.tagName === 'FIELDSET'); assert.ok(fsets.length >= 4 && fsets.every((f) => f.disabled), 'çalışırken düzenleme kapalı');
  assert.strictEqual(btn(r2, 'Duraklat').disabled, false);
  assert.ok(all(r2, (n) => n.className.includes('pg-step') && n.className.includes('run')).length === 1, 'çalışan satır vurgulu');
  b.def.onHide();
  assert.ok(b.calls.some((c) => c.path === '/api/program/pause'), 'sayfa gizlenince program duraklatılır (K3h)');
  assert.ok(b.timers.every((t) => t === null), 'gizlenince zamanlayıcılar durur');
  runState = { state: 'idle', running: false }; b.def.onShow(); await flush();
  runReply = () => { throw Object.assign(new Error('Başka bir program çalışıyor'), { status: 409, body: { ok: false, error: 'busy', message: 'Başka bir program çalışıyor' } }); };
  b.def.onState(snap()); btn(r2, 'Önizle').click(); await flush(); btn(r2, 'Çalıştır').click(); await flush();
  assert.ok(b.toasts.some((t) => t.bad && /çalışıyor/.test(t.msg)), '409 busy kullanıcıya gösterilir');
  b.def.onState(snap({ estop: { latched: true } }));
  assert.strictEqual(btn(r2, 'Çalıştır').disabled, true); assert.match(r2.textContent, /ACİL DUR aktif/);

  /* ---- 4) HubScene (§9-C): numaralı işaret, yol çizgisi, hedef çerçevesi + gizmo, onPick; kimlikler `program:` önekli ---- */
  const c = load({}, { scene: true }); const r3 = new N('section'); await c.def.mount(r3); await flush(); c.def.onState(snap());
  const adds = (id) => c.sceneCalls.filter(([k, o]) => k === 'add' && o.id === id).length, S = c.H;
  assert.ok(S.objs.has('program:target'), 'hedef çerçevesi eklendi'); assert.strictEqual(S.objs.get('program:target').kind, 'frame');
  assert.ok(c.sceneCalls.some(([k, id]) => k === 'gizmo' && id === 'program:target'), 'hedef için gizmo açıldı');
  assert.deepStrictEqual(S.pickLayers, ['workpiece', 'environment'], 'işaret/hedef seçilemez, parça yüzeyi seçilir');
  await btn(r3, 'Şu anki pozu kaydet').click(); await flush();
  const m1 = S.objs.get('program:wp1');
  assert.ok(m1 && m1.kind === 'marker' && m1.geometry.number === 1 && m1.layer === 'markers', 'numaralı işaret eklendi');
  assert.deepStrictEqual(m1.pose.pos_m, [0.3, 0.1, 0.2], 'işaret waypoint TCP konumunda');
  assert.strictEqual(r3.textContent.includes('3B sahne:'), false, 'hata yok');
  await btn(r3, 'Şu anki pozu kaydet').click(); await flush();
  const line = S.objs.get('program:path');
  assert.ok(S.objs.has('program:wp2') && line && line.kind === 'polyline' && line.geometry.points_m.length === 2, 'iki waypoint -> yol çizgisi');
  assert.strictEqual(adds('program:target'), 1, 'hedef çerçevesi her waypoint değişiminde YENİDEN kurulmaz (gizmo kopmasın)');
  S.pick({ objectId: 'parts:abc', point_m: [0.2, 0.1, 0.3], normal: [0, 0, 1] }); await flush(450);
  assert.ok(c.calls.some((x) => x.path === '/api/program/check_move' && x.body.step.pos_m[0] === 0.2), 'onPick hedefi ulaşılabilirlik denetiminden geçer');
  assert.deepStrictEqual(S.objs.get('program:target').pose.pos_m, [0.2, 0.1, 0.3], 'dokunulan noktada hedef çerçevesi');
  assert.strictEqual(btn(r3, 'Waypoint olarak ekle').disabled, false, 'yeşil hedef eklenebilir');
  const poses = c.sceneCalls.filter(([k]) => k === 'pose').length, checks = () => c.calls.filter((x) => x.path === '/api/program/check_move').length, before = checks();
  S.gizmo({ id: 'parts:abc', pose: { pos_m: [0.9, 0.9, 0.9], rpy_deg: [0, 0, 0] }, phase: 'move' }); await flush(450);
  assert.strictEqual(checks(), before, 'başka sayfanın gizmo olayı hedefi değiştirmez');
  S.gizmo({ id: 'program:target', pose: { pos_m: [0.25, 0.12, 0.3], rpy_deg: [0, 90, 0] }, phase: 'move' }); await flush(450);
  assert.ok(c.calls.some((x) => x.path === '/api/program/check_move' && x.body.step.pos_m[0] === 0.25 && x.body.step.rpy_deg[1] === 90), 'gizmo hedefi denetime sokar');
  assert.strictEqual(c.sceneCalls.filter(([k]) => k === 'pose').length, poses, 'sürüklerken çerçeve yeniden konumlanmaz/kurulmaz');
  assert.strictEqual(adds('program:target'), 1);
  c.def.onHide();
  assert.strictEqual(S.objs.size, 0, 'sayfadan çıkınca TÜM nesneler kalkar'); assert.ok(c.sceneCalls.some(([k]) => k === 'nogizmo'), 'gizmo kapanır');
  assert.strictEqual(S.pickLayers.length, 4, 'seçim katmanları varsayılana döner');
  c.def.onShow(); await flush(); assert.ok(S.objs.has('program:wp1') && S.objs.has('program:target'), 'sayfaya dönünce işaretler geri gelir');

  /* ---- 5) 3B yok / sahne reddediyor: sayfa yine çalışır, hata SESSİZ DEĞİL ---- */
  const off = load({}, { scene: { implemented: false } }), r4 = new N('section'); await off.def.mount(r4); await flush(); off.def.onState(snap());
  await btn(r4, 'Şu anki pozu kaydet').click(); await flush();
  assert.strictEqual(off.sceneCalls.length, 0, 'WebGL yoksa sahneye dokunulmaz'); assert.strictEqual(byTitle(r4, 'Waypoint adı').length, 1, 'tablo çalışır');
  const rej = load({}, { scene: { reject: 'kimlik geçersiz' } }), r5 = new N('section'); await rej.def.mount(r5); await flush(); rej.def.onState(snap());
  await btn(r5, 'Şu anki pozu kaydet').click(); await flush();
  assert.match(r5.textContent, /3B sahne: kimlik geçersiz/, 'sahne {ok:false} dönerse kullanıcı görür');

  /* ---- 6) arka uç: SİM'de "SAHTE" bandı YOK, mock'ta var ---- */
  const banner = (root) => all(root, (n) => n.className === 'banner')[0];
  assert.strictEqual(banner(root).hidden, false, 'mock: bant görünür');
  const sim = load({ 'GET /api/program/state': () => ({ ok: true, runner: { state: 'idle', running: false }, estop: false,
    pose: { joints_deg: [0, 0, 0, 0, 0, 0] }, backend: { label: 'SİM', measured: false, mock: false }, limits: {} }) });
  const r6 = new N('section'); await sim.def.mount(r6); await flush(); sim.def.onState(snap());
  assert.strictEqual(banner(r6).hidden, true, 'SİM: SAHTE bandı görünmez');
  /* ---- 7) Algoritmalar'dan aktarım (HubProgram.addSteps) ve orientation:'free' ---- */
  const FREE = { kind: 'move_l', pos_m: [0.1, 0, 0.08], orientation: 'free' };
  const t0 = load({ 'POST /api/program/check': () => PREVIEW_OK }); await flush();              // mantık betiği yüklensin (sayfa açılmadan)
  assert.strictEqual(typeof window.HubProgram.addSteps, 'function');
  assert.strictEqual(window.ProgramLogic.total([{ kind: 'wait' }, { kind: 'loop', steps: [{}, {}] }]), 4, 'döngü gövdesi sayılır');
  assert.deepStrictEqual(window.HubProgram.addSteps([]), { ok: false, message: 'Eklenecek adım yok' });
  assert.strictEqual(window.HubProgram.addSteps('x').ok, false);
  const early = window.HubProgram.addSteps([FREE, { kind: 'tool', open_pct: 30 }]);        // sayfa henüz AÇILMADAN
  assert.deepStrictEqual(early, { ok: true, total: 2 });
  const r7 = new N('section'); await t0.def.mount(r7); await flush(); t0.def.onState(snap());
  assert.strictEqual(all(r7, (n) => n.className.includes('pg-step')).length, 2, 'açılış öncesi eklenen adımlar sayfada görünür');
  assert.match(r7.textContent, /yönelim: serbest/, 'serbest yönelim etiketi');
  assert.strictEqual(byTitle(r7, 'rpy (°)').length, 0, 'serbest adımda rpy alanı YOK');
  assert.strictEqual(byTitle(r7, 'x,y,z (m)').length, 1, 'konum alanı var');
  btn(r7, 'Önizle').click(); await flush();
  assert.strictEqual(btn(r7, 'Çalıştır').disabled, false, 'önizleme geçti');
  const more = window.HubProgram.addSteps([{ kind: 'comment', text: 'sonra' }]);          // sayfa açıkken
  assert.deepStrictEqual(more, { ok: true, total: 3 });
  assert.strictEqual(btn(r7, 'Çalıştır').disabled, true, 'eklemek önizlemeyi geçersiz kılar');
  assert.strictEqual(all(r7, (n) => n.className.includes('pg-step')).length, 3);
  assert.match(r7.textContent, /kaydedilmemiş/, 'ekleme kaydedilmemiş değişiklik sayılır');
  const src = byTitle(r7, 'Hedef kaynağı')[0]; src.value = 'wp'; src.fire('change');       // Değer -> Waypoint
  assert.ok(!/yönelim: serbest/.test(r7.textContent), 'kaynak değişince serbest yönelim bayrağı silinir (waypoint kendi yönelimini taşır)');
  const big = Array.from({ length: 498 }, () => ({ kind: 'wait', s: 1 }));
  const refused = window.HubProgram.addSteps(big);                                        // 3 + 498 > 500
  assert.strictEqual(refused.ok, false); assert.match(refused.message, /En çok 500/);
  assert.strictEqual(all(r7, (n) => n.className.includes('pg-step')).length, 3, 'reddedilince hiçbir adım eklenmez');
  const mine = { kind: 'move_l', pos_m: [0.1, 0, 0.08], orientation: 'free' };
  assert.strictEqual(window.HubProgram.addSteps([mine]).ok, true);
  mine.pos_m[0] = 9;                                                                      // kaynak sonradan değişir
  assert.strictEqual(window.HubProgram.addSteps([{ kind: 'comment', text: 'yeniden çiz' }]).ok, true);   // sayfa yeniden çizilir
  assert.deepStrictEqual(byTitle(r7, 'x,y,z (m)').map((n) => n.value).pop(), '0.1, 0, 0.08', 'aktarılan adımlar KOPYALANIR');
  // ad: adsız programa varsayılan ad yazılır, kullanıcının/önceki adı EZİLMEZ (Önizle/Kaydet ad ister)
  const nameIn = () => byTitle(r7, 'Program adı')[0];
  assert.strictEqual(nameIn().value, '', 'ad verilmeyen eklemeler adı boş bırakır');
  assert.strictEqual(window.HubProgram.addSteps([{ kind: 'comment', text: 'a' }], 42).ok, true);
  assert.strictEqual(nameIn().value, '', 'dize olmayan ad yok sayılır');
  window.HubProgram.addSteps([{ kind: 'comment', text: 'b' }], '  Daire yay  ');
  assert.strictEqual(nameIn().value, 'Daire yay', 'adsız programa varsayılan ad yazılır (kırpılmış)');
  window.HubProgram.addSteps([{ kind: 'comment', text: 'c' }], 'Zigzag tarama');
  assert.strictEqual(nameIn().value, 'Daire yay', 'dolu ad ezilmez');
  nameIn().value = 'Benim programım'; nameIn().fire('change');
  window.HubProgram.addSteps([{ kind: 'comment', text: 'd' }], 'Izgara');
  assert.strictEqual(nameIn().value, 'Benim programım', 'kullanıcının yazdığı ad ezilmez');
  const t1 = load({ 'GET /api/program/state': () => ({ ok: true, runner: { state: 'running', running: true, line: 0, sub: null, done: 0, total: 1, say: '' },
    estop: false, pose: null, backend: { label: 'SİM', measured: false, mock: false }, limits: {} }) });
  const r8 = new N('section'); await t1.def.mount(r8); await flush(); t1.def.onState(snap());
  assert.deepStrictEqual(window.HubProgram.addSteps([FREE]), { ok: false, message: 'Program çalışıyor: bitince ekle' }, 'çalışırken ekleme reddedilir');
  console.log('PROGRAM_PAGE_OK'); process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
