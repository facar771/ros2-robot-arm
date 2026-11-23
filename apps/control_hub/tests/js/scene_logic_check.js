/* 3B görünüm mantığı (scene_logic.js + shell.js saf işlevleri). pytest çağırır (tests/test_ui_skeleton.py).
 *   node scene_logic_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);
global.window = global;
require(path.join(WEB, 'scene_logic.js'));
require(path.join(WEB, 'shell.js'));
const L = SceneLogic, H = ShellLogic;

/* --- STL --- */
function stl(tris) {
  const b = new ArrayBuffer(84 + tris.length * 50), dv = new DataView(b);
  dv.setUint32(80, tris.length, true);
  tris.forEach((t, i) => { const o = 84 + i * 50; [0, 0, 1].forEach((v, k) => dv.setFloat32(o + 4 * k, v, true));
    t.flat().forEach((v, k) => dv.setFloat32(o + 12 + 4 * k, v, true)); });
  return b;
}
const tri = [[0, 0, 0], [1, 0, 0], [0, 1, 0]];
const p = L.parseStl(stl([tri, tri]));
assert.strictEqual(p.triangles, 2); assert.strictEqual(p.positions.length, 18); assert.strictEqual(p.normals[2], 1);
assert.strictEqual(p.positions[3], 1);
assert.throws(() => L.parseStl(new ArrayBuffer(10)), /kısa/);
assert.throws(() => L.parseStl(stl([tri]).slice(0, 120)), /ikili/);                  // boyut başlıkla uyuşmuyor
const ascii = new TextEncoder().encode('solid x\n' + 'facet normal 0 0 1\n'.repeat(10)).buffer;
assert.throws(() => L.parseStl(ascii), /ikili|kısa/);
const nan = stl([tri]); new DataView(nan).setFloat32(84 + 12, NaN, true);
assert.throws(() => L.parseStl(nan), /sonlu/);

/* --- eklem sırası --- */
const m = { root: 'b', joints: [{ name: 'j2', parent: 'l1', child: 'l2' }, { name: 'j1', parent: 'b', child: 'l1' }] };
assert.deepStrictEqual(L.orderJoints(m).map((j) => j.name), ['j1', 'j2']);              // sırasız model de çizilir
assert.throws(() => L.orderJoints({ root: 'b', joints: [{ name: 'x', parent: 'yok', child: 'c' }] }), /üst çerçeve yok/);
assert.throws(() => L.orderJoints({ root: 'b', joints: [{ name: 'x', parent: 'b', child: 'c' }, { name: 'y', parent: 'b', child: 'c' }] }), /iki kez/);

/* --- model/durum eşleşmesi: yanlış eklemi çizme --- */
const snap = (js) => ({ robot: { joints: js } });
assert.strictEqual(L.jointProblem(null, null, 'robot yok'), 'robot yok');
assert.match(L.jointProblem(m, snap([]), null), /gelmiyor/);
assert.match(L.jointProblem(m, snap([{ name: 'j1', rad: 0 }]), null), /j2/);
assert.match(L.jointProblem(m, snap([{ name: 'j1', rad: 0 }, { name: 'j2', rad: null }]), null), /j2/);
assert.strictEqual(L.jointProblem(m, snap([{ name: 'j1', rad: 0 }, { name: 'j2', rad: 1 }]), null), null);

/* --- bayatlık (yalnız WS canlılığı; HTTP yoklaması taze yapmaz) --- */
const ok = { robot: { connected: true } };
assert.ok(L.staleReason(5000, 0, 0, ok));                                              // hiç mesaj yok
assert.ok(L.staleReason(5000, 3900, 3900, ok));                                        // >1 s sessiz
assert.strictEqual(L.staleReason(5000, 4500, 4500, ok), null);
assert.match(L.staleReason(5000, 4900, 4900, { robot: { connected: false, problem: 'COM yok' } }), /COM yok/);
assert.match(L.staleReason(5000, 4900, 4900, { robot: { connected: true, stale_s: 1.2 } }), /eski/);   // robot verisi 1 s'den eski
assert.strictEqual(L.staleReason(5000, 4900, 4950, { robot: { connected: true, stale_s: 0.2 } }), null);

/* --- yumuşatma --- */
assert.strictEqual(L.approach(0, 1, 0), 0);
assert.ok(L.approach(0, 1, 0.1) > 0.6 && L.approach(0, 1, 0.1) < 0.7);
assert.ok(Math.abs(L.approach(0, 1, 5) - 1) < 1e-6);

/* --- kabuk: mod / erişim / bağlantı --- */
assert.strictEqual(H.modeInfo({ robot: { mode: 'real' } }).key, 'real');
assert.strictEqual(H.modeInfo({ robot: { mode: 'fake' } }).key, 'sim');
assert.strictEqual(H.modeInfo({ robot: { mode: 'sim' } }).label, 'SİM');
assert.strictEqual(H.modeInfo({ robot: { mode: 'real' }, bench: { enabled: true, open: true } }).key, 'bench');   // açık tezgâh öncelikli
assert.strictEqual(H.modeInfo({ robot: { mode: 'fake' }, bench: { enabled: true, open: false } }).key, 'sim');
assert.strictEqual(H.modeInfo(null).key, 'unknown');
assert.strictEqual(H.accessInfo('pin', true).label, 'PIN ✓'); assert.strictEqual(H.accessInfo('pin', false).label, 'PIN gerekli');
assert.strictEqual(H.accessInfo('locked').key, 'locked'); assert.strictEqual(H.accessInfo('open').key, 'open');
assert.strictEqual(H.connInfo('live', { robot: { connected: true } }).state, 'live');
assert.strictEqual(H.connInfo('live', { robot: { connected: false } }).state, 'warn');
assert.strictEqual(H.connInfo('live', { robot: { connected: true, compatible: false } }).state, 'warn');
assert.strictEqual(H.connInfo('down', { robot: { connected: true } }).state, 'down');   // WS yoksa robot "bağlı" yazsa da canlı DEĞİL
assert.strictEqual(H.connInfo('connecting', null).state, 'connecting');

/* --- sayfa sırası ve adres --- */
const ids = (l) => H.sortPages(l).map((x) => x.id);
assert.deepStrictEqual(ids([{ id: 'b', order: 20 }, { id: 'a', order: 20 }, { id: 'z', order: 5 }, { id: 'n' }]), ['z', 'a', 'b', 'n']);
assert.strictEqual(H.pageFromHash('#/jog'), 'jog');
for (const bad of ['', '#', '#/', '#/Jog', '#/../x', '#/a b', '#/1x', null, undefined]) assert.strictEqual(H.pageFromHash(bad), null);

console.log('SCENE_LOGIC_JS_OK');
