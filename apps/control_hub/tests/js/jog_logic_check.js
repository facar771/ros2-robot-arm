/* Kontrol sayfasının saf mantığı + jog motoru (sahte DOM/api): basılı-tut bırakınca durur, adım modu önce kontrol eder,
 * ihlalde hareket komutu GİTMEZ. node jog_logic_check.js <web klasörü> */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);
global.window = global;
const L = require(path.join(WEB, 'jog_logic.js'));

// --- P1b: tıkla-git saf mantığı ---
const Z = { min: [0.06, -0.1, 0.02], max: [0.22, 0.1, 0.22] };
assert.ok(Math.abs(L.defaultZ(Z) - 0.12) < 1e-9);                                  // varsayılan z = güvenli kutu ortası
assert.strictEqual(L.clampZ(Z, 0.5), 0.22); assert.strictEqual(L.clampZ(Z, -1), 0.02); assert.ok(Math.abs(L.clampZ(Z, NaN) - 0.12) < 1e-9);
assert.strictEqual(L.zoneOf({}), null); assert.strictEqual(L.zoneOf({ safe_zone: { min: [0, 0, 0], max: [0, 1, 1] } }), null);
assert.strictEqual(L.zoneOf({ safe_zone: Z }), Z);
const ps = L.planeSpec(Z, 0.12);
assert.ok(ps.id.startsWith('jog:') && L.markerSpec([0, 0, 0]).id.startsWith('jog:'));
assert.deepStrictEqual(ps.pose.pos_m, [0.14, 0, 0.12]); assert.ok(Math.abs(ps.geometry.size_m[0] - 0.16) < 1e-9);
assert.deepStrictEqual(L.pickToTarget({ objectId: 'jog:plane', point_m: [0.1, 0.05, 0.12] }, Z, 0.1), [0.1, 0.05, 0.1]);  // z alandan
assert.strictEqual(L.pickToTarget({ objectId: 'parts:x', point_m: [0.1, 0, 0.1] }, Z, 0.1), null);                       // başka nesne
assert.strictEqual(L.pickToTarget({ objectId: 'jog:plane', point_m: [0.5, 0, 0.1] }, Z, 0.1), null);                     // kutu dışı
assert.strictEqual(L.pickToTarget({ objectId: 'jog:plane', point_m: [NaN, 0, 0.1] }, Z, 0.1), null);
assert.strictEqual(L.pickToTarget({ objectId: 'jog:plane', point_m: [0.1, 0, 0.1] }, null, 0.1), null);
assert.strictEqual(L.orientationFor('sim'), 'free'); assert.strictEqual(L.orientationFor('real'), 'fixed'); assert.strictEqual(L.orientationFor('bench'), 'fixed');
assert.deepStrictEqual(L.linearBody([0.1, 0, 0.1], 500, 'sim'), { type: 'linear', pos_m: [0.1, 0, 0.1], orientation: 'free', speed_pct: 100 });
assert.strictEqual(L.linearBody([0, 0, 0], 50, 'real').orientation, 'fixed'); assert.ok(!('rpy_deg' in L.linearBody([0, 0, 0], 50, 'sim')));
const okS = { me: { can_control: true }, estop: { latched: false }, robot: { safe_zone: Z } };
assert.strictEqual(L.targetDisabledReason(okS), null);
assert.ok(L.targetDisabledReason({ ...okS, estop: { latched: true } }).includes('423'));                                 // ACİL DUR: kapalı
assert.ok(L.targetDisabledReason({ ...okS, robot: {} }).includes('Güvenli kutu'));

// --- saf işlevler ---
assert.deepStrictEqual(L.sliderState(0, -170, 170), { frac: 0.5, out: false, near: false });
assert.strictEqual(L.sliderState(-165, -170, 170).near, true);              // aralığın %5'i = 17°
assert.strictEqual(L.sliderState(166, -170, 170).near, true);
assert.strictEqual(L.sliderState(200, -170, 170).out, true);
assert.strictEqual(L.sliderState(NaN, -170, 170).out, true);
assert.strictEqual(L.sliderState(0, 5, 5).out, true);                       // min == max: bozuk aralık
assert.strictEqual(L.stepTarget(10, -1, 0.1), 9.9);
assert.deepStrictEqual(L.holdTwist(2, -1, 100), [0, 0, -0.02, 0, 0, 0]);
assert.deepStrictEqual(L.holdTwist(3, 1, 50), [0, 0, 0, 0.05, 0, 0]);
assert.strictEqual(L.clampPct(0), 1); assert.strictEqual(L.clampPct(500), 100); assert.strictEqual(L.clampPct('x'), 50);
assert.deepStrictEqual(L.moveBody([1.00049, 2], 200), { type: 'joint', joints_deg: [1, 2], speed_pct: 100 });
assert.strictEqual(L.checkVerdict({ ok: true, violations: [] }).ok, true);
const bad = L.checkVerdict({ ok: false, violations: [{ kind: 'joint_limit', at_pct: 40, message: 'sınır' }] });
assert.ok(!bad.ok && bad.text.includes('sınır') && bad.text.includes('%40'));
assert.strictEqual(L.checkVerdict(null).ok, false);
assert.ok(L.explain({ status: 423, body: { message: 'x' }, message: '' }).includes('ACİL DUR'));
assert.strictEqual(L.disabledReason({ me: { can_control: true }, estop: { latched: true } }).includes('423'), true);
assert.strictEqual(L.disabledReason({ me: { can_control: true }, estop: { latched: false } }), null);
assert.ok(L.disabledReason({ me: { can_control: false } }).includes('salt okunur'));
assert.strictEqual(L.badge({ mode: 'sim' }).text, 'SİM'); assert.strictEqual(L.badge({ mode: 'bench' }).text, 'TEZGÂH');
assert.strictEqual(L.badge({ mode: 'real', maturity: 'olculdu' }).estimated, false);
assert.strictEqual(L.badge(undefined).text, 'SİM'); assert.strictEqual(L.badge({ mode: 'sim', stale: true }).stale, true);
const rows = L.poseRows({ joints: [{ name: 'joint_1', deg: 1.234, min_deg: -170, max_deg: 170, near_limit: true }],
                          tcp: { pos_m: [0.1, 0.2, 0.3], rpy_deg: [1, 2, 3] } });
assert.deepStrictEqual(rows.joints[0], { name: 'joint_1', deg: '1.2', min: '-170', max: '170', near: true });
assert.deepStrictEqual(rows.tcp.mm, ['100.0', '200.0', '300.0']);
assert.strictEqual(L.poseRows({}).tcp, null);
assert.strictEqual(L.jointsUsable([{ deg: 0, min_deg: -1, max_deg: 1 }]), true);
assert.strictEqual(L.jointsUsable([{ deg: 0 }]), false); assert.strictEqual(L.jointsUsable([]), false);
assert.strictEqual(L.jointsUsable([{ deg: 0, min_deg: 1, max_deg: 1 }]), false);
assert.strictEqual(L.gripperPct({ gripper: { open_pct: 30 } }), 30); assert.strictEqual(L.gripperPct({}), null);
assert.strictEqual(L.homeDeg({ home_deg: [0, 1] }).length, 2); assert.strictEqual(L.homeDeg({ home_deg: [0, 'x'] }), null);

// --- jog motoru: sahte DOM ---
class N {
  constructor(t) { this.tagName = t; this.children = []; this._text = ''; this.attrs = {}; this.listeners = {}; this.dataset = {};
    this.classList = { toggle() {}, add() {}, remove() {} }; this.disabled = false; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  remove() {}
  setAttribute(k, v) { this.attrs[k] = String(v); }
  setPointerCapture() {}
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t, ev = {}) { for (const f of this.listeners[t] || []) f({ button: 0, pointerId: 1, preventDefault() {}, ...ev }); }
  get textContent() { return this._text; } set textContent(v) { this._text = String(v); }
}
global.el = (tag, props = {}, children = []) => {
  const e = new N(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'text') e.textContent = v; else if (k.startsWith('data-')) e.dataset[k.slice(5)] = v; else e[k] = v; }
  e.append(...children); all.push(e); if (props.id) byId[props.id] = e; return e;
};
const byId = {}, all = [];
/* küçük seçici: '... .sınıf' ya da '.sınıf[data-k="v"]' (jog.js'in kullandığı kadar) */
function select(sel) {
  const last = sel.trim().split(/\s+/).pop();
  const m = last.match(/^\.([\w-]+)(?:\[data-([\w-]+)="([^"]*)"\])?$/);
  return all.filter((n) => m && String(n.class || '').split(/\s+/).includes(m[1]) && (!m[2] || n.dataset[m[2]] === m[3]));
}
global.document = { getElementById: (id) => byId[id] || null, querySelectorAll: select, querySelector: (s) => select(s)[0] || null,
                    addEventListener() {}, hidden: false };
global.addEventListener = () => {};
const calls = []; let next = () => ({});
global.api = async (url, method = 'GET', body) => { calls.push({ url, method, body }); const r = next(url, body); if (r instanceof Error) throw r; return r; };
let timers = []; let tid = 0;
global.setInterval = (f, ms) => { timers.push({ f, ms, id: ++tid }); return tid; };
global.clearInterval = (id) => { timers = timers.filter((t) => t.id !== id); };

require(path.join(WEB, 'jog.js'));
const err = (status, message) => Object.assign(new Error(message), { status, body: { message } });
const flush = () => new Promise((r) => setImmediate(r));
(async () => {
  // (1) Git: önce /api/move/check, ihlal varsa /api/move HİÇ çağrılmaz
  next = (url) => (url === '/api/move/check' ? { ok: false, violations: [{ message: 'kutu dışı', at_pct: 100 }] } : {});
  let r = await JogPanel.goJoints([1, 2, 3]);
  assert.strictEqual(r.ok, false); assert.ok(r.text.includes('kutu dışı'));
  assert.deepStrictEqual(calls.map((c) => c.url), ['/api/move/check']);                 // hareket komutu gitmedi
  calls.length = 0;
  next = (url) => (url === '/api/move/check' ? { ok: true, violations: [] } : { ok: true, job_id: 'j1' });
  r = await JogPanel.goJoints([1, 2, 3]);
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(calls.map((c) => c.url), ['/api/move/check', '/api/move']);   // sıra: önce kontrol
  assert.strictEqual(calls[1].body.type, 'joint'); assert.strictEqual(calls[1].body.speed_pct, 50);
  // 423 hareket çağrısında düz Türkçe metne çevrilir
  calls.length = 0;
  next = (url) => (url === '/api/move' ? err(423, 'kilitli') : { ok: true, violations: [] });
  r = await JogPanel.goJoints([0]); assert.ok(!r.ok && r.text.includes('423'));
  // (2) mount + basılı-tut: kip kapalıyken pointerdown komut göndermez; sürekli kipte heartbeat, bırakınca ttl_ms:0
  const host = el('div');
  next = (url) => (url === '/api/jog' ? { enabled: true, hold_allowed: true, hold_reason: null, frames: [] }
                   : url === '/api/frames' ? { frames: [{ name: 'Tezgah' }] } : { ok: true });
  JogPanel.mount(host); await flush(); await flush();
  JogPanel.onState({ me: { can_control: true }, estop: { latched: false }, robot: { mode: 'sim', joints: [{ deg: 10, min_deg: -170, max_deg: 170 }] } });
  const btn = JogPanel.jointButton(0, -1, '−');
  calls.length = 0; btn.fire('pointerdown'); await flush();
  assert.strictEqual(calls.length, 0); assert.strictEqual(timers.length, 0);          // adım kipi: basılı-tut YOK
  select('.jp-mode').find((b) => b.dataset.hold === '1').fire('click');                // sürekli kip
  btn.fire('pointerdown'); await flush();
  assert.strictEqual(calls[0].url, '/api/jog/joint');
  assert.deepStrictEqual([calls[0].body.joint, calls[0].body.dir, calls[0].body.ttl_ms], [0, -1, 250]);
  assert.strictEqual(timers.length, 1); assert.strictEqual(timers[0].ms, 100);          // 100 ms heartbeat
  timers[0].f(); await flush();                                                         // ikinci vuruş
  assert.ok(calls.filter((c) => c.body.ttl_ms === 250).length >= 1);
  btn.fire('pointerup'); await flush();                                                 // bırakınca DUR
  assert.strictEqual(timers.length, 0);
  assert.strictEqual(calls[calls.length - 1].body.ttl_ms, 0);
  assert.strictEqual(calls[calls.length - 1].url, '/api/jog/joint');
  // lostpointercapture (parmak kaydı ya da sekme kapandı) aynı şekilde durdurur
  calls.length = 0; btn.fire('pointerdown'); await flush(); btn.fire('lostpointercapture'); await flush();
  assert.strictEqual(timers.length, 0); assert.strictEqual(calls[calls.length - 1].body.ttl_ms, 0);
  // kartezyen eksen düğmesi: twist hız % ile ölçeklenir; 403 gelirse sürekli kip kendini kapatır
  calls.length = 0;
  const axis = select('.jp-b').find((b) => b.dataset.axis === '2' && b.dataset.sign === '1');
  next = (url) => (url === '/api/jog' ? err(403, 'yalnızca yerel') : { ok: true });
  axis.fire('pointerdown'); await flush(); await flush();
  assert.deepStrictEqual(calls[0].body.twist, [0, 0, 0.01, 0, 0, 0]);                  // %50 x 0.02
  assert.strictEqual(timers.length, 0);                                                 // 403 -> durdu
  // bırak düğmesi: iki ucu da ttl_ms:0 ile çağırır
  calls.length = 0; next = () => ({ ok: true }); byId['jp-release'].fire('click'); await flush(); await flush();
  assert.deepStrictEqual(calls.map((c) => c.url), ['/api/jog', '/api/jog/joint']);
  assert.ok(calls.every((c) => c.body.ttl_ms === 0));
  console.log('JOG_LOGIC_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
