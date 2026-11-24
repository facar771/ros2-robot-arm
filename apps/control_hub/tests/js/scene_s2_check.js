/* S2 saf mantığı (scene_logic.js): SE(3), dokun-git hedefi, nesne şeması (§9-C kimlikleri), yardımcılar; kontrol->onay->hareket akışı approach.js'te (approach_check.js).
 *   node scene_s2_check.js <web dizini> [robot_core_matrisleri.json]
 * pytest çağırır (tests/test_approach.py). İkinci argüman: robot_core.se3'ün ürettiği (rpy, matris) çiftleri. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(process.argv[2]);
global.window = global;
require(path.join(WEB, 'scene_logic.js'));
const L = SceneLogic;
const near = (a, b, tol = 1e-9) => assert.ok(Math.abs(a - b) <= tol, `${a} ≉ ${b}`);
const nearV = (a, b, tol = 1e-9) => a.forEach((x, i) => near(x, b[i], tol));
const det3 = (m) => m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6]);

(async () => {
  /* --- SE(3): robot_core.se3 ile aynı sözleşme --- */
  nearV(L.rpyToMatrix([0, 0, 90]).map((x) => +x.toFixed(12)), [0, -1, 0, 1, 0, 0, 0, 0, 1]);           // yaw 90: x -> y
  nearV(L.rpyToMatrix([90, 0, 0]).map((x) => +x.toFixed(12)), [1, 0, 0, 0, 0, -1, 0, 1, 0]);           // roll 90: y -> z
  for (const rpy of [[10, 20, 30], [-170, 45, 120], [0, -80, 5], [179, 0, -179], [33, 88, -60]]) {
    nearV(L.matrixToRpy(L.rpyToMatrix(rpy)), rpy, 1e-7);
  }
  const lock = L.matrixToRpy(L.rpyToMatrix([30, 90, 40]));                                                // gimbal: yaw=0, dönüş roll'da
  assert.ok(Math.abs(lock[1] - 90) < 1e-6 && lock[2] === 0);
  nearV(L.rpyToMatrix(lock), L.rpyToMatrix([30, 90, 40]), 1e-9);                                         // aynı dönüş
  assert.ok(L.nearGimbal([0, 85, 0]) && !L.nearGimbal([0, 45, 0]));
  assert.throws(() => L.rpyToMatrix([1, 2]), /3 sonlu/);
  if (process.argv[3]) {                                                                                  // robot_core ile çapraz doğrulama
    const ref = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
    assert.ok(ref.forward.length >= 10 && ref.inverse.length >= 10);
    for (const { rpy, matrix } of ref.forward) nearV(L.rpyToMatrix(rpy), matrix.flat(), 1e-9);          // rpy -> matris
    for (const { rpy, matrix } of ref.inverse) nearV(L.matrixToRpy(matrix.flat()), rpy, 1e-6);           // matris -> rpy (gimbal dahil)
  }
  const a = L.poseOf([0.1, 0.2, 0.3], [10, 20, 30]), b = L.poseOf([-0.3, 0.1, 0.5], [-40, 5, 70]);
  const ab = L.poseMul(a, L.poseInv(a));
  nearV(ab.R, [1, 0, 0, 0, 1, 0, 0, 0, 1], 1e-12); nearV(ab.p, [0, 0, 0], 1e-12);
  const round = L.poseMul(L.poseInv(L.poseMul(a, b)), L.poseMul(a, b));
  nearV(round.R, [1, 0, 0, 0, 1, 0, 0, 0, 1], 1e-12);

  /* --- dokun-git / yaklaş: hedef = nokta + normal·d --- */
  const P = [0.3, 0.1, 0.1], N = [0, 0, 1];                                                               // z = 0.1 düzlemi, normal +Z
  const g0 = L.approachTarget(P, N, 0), g50 = L.approachTarget(P, N, 50);
  nearV(g0.pos_m, [0.3, 0.1, 0.1]); nearV(g50.pos_m, [0.3, 0.1, 0.15]);                                   // d = 0 ve d = 50 mm
  nearV(g50.rpy_deg, [180, 0, 0], 1e-9);                                                                  // alet aşağı bakar
  nearV(L.approachTarget(P, [0, 0, 5], 50).pos_m, g50.pos_m);                                             // normal birimlenir
  nearV(L.approachTarget(P, N, 50, { orientation: 'down' }).rpy_deg, [180, 0, 0], 1e-9);
  nearV(L.approachTarget(P, N, 50, { orientation: 'keep', currentRpy: [1, 2, 3] }).rpy_deg, [1, 2, 3]);
  assert.throws(() => L.approachTarget(P, N, 50, { orientation: 'keep' }), /mevcut yönelim/);
  assert.throws(() => L.approachTarget(P, N, -1), /mesafe/);
  assert.throws(() => L.approachTarget(P, N, 1001), /mesafe/);
  assert.throws(() => L.approachTarget(P, N, NaN), /mesafe/);
  assert.throws(() => L.approachTarget(P, [0, 0, 0], 10), /sıfır/);
  assert.throws(() => L.approachTarget([0, 'x', 0], N, 10), /3 sonlu/);
  assert.throws(() => L.approachTarget(P, N, 10, { orientation: 'sol' }), /yönelim/);
  for (let i = 0; i < 200; i++) {                                                                         // rastgele normallerde: z_alet = −n, dönme matrisi
    const n = L.unit([Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5]);
    const t = L.approachTarget([0, 0, 0], n, 30), R = L.rpyToMatrix(t.rpy_deg);
    nearV([R[2], R[5], R[8]], n.map((x) => -x), 1e-9); near(det3(R), 1, 1e-9);
    nearV(t.pos_m, n.map((x) => x * 0.03), 1e-12);
  }
  nearV(L.approachTarget([0.5, 0, 0.2], [-1, 0, 0], 50).pos_m, [0.45, 0, 0.2]);                           // duvara yan yaklaşma
  assert.deepStrictEqual(L.facingNormal([0, 0, 1], [0, 0, -1]), [0, 0, 1]);                               // ışın aşağı, normal yukarı: kalır
  assert.deepStrictEqual(L.facingNormal([0, 0, -1], [0, 0, -1]), [0, 0, 1]);                              // içe bakan normal çevrilir
  const tcp0 = { pos_m: [0, 0, 0], rpy_deg: [0, 0, 0] }, tgt = { pos_m: [0.3, 0.1, 0.15], rpy_deg: [180, 0, 0] };
  const gh = L.ghostToolPose(tgt, tcp0, tcp0); nearV(gh.pos_m, tgt.pos_m); nearV(gh.rpy_deg, tgt.rpy_deg, 1e-9);
  const off = { pos_m: [0, 0, 0.1], rpy_deg: [0, 90, 0] };
  const same = L.ghostToolPose(tgt, off, off); nearV(same.pos_m, tgt.pos_m, 1e-12);                       // takım ofseti = TCP ofseti: hayalet hedefin kendisi
  const shifted = L.ghostToolPose(tgt, off, tcp0);                                                         // takım flanşta: hedefin 0,1 m gerisinde
  assert.ok(Math.hypot(...shifted.pos_m.map((v, i) => v - tgt.pos_m[i])) > 0.09);

  /* --- nesne şeması --- */
  const v = L.validateSpec({ id: 'x:p1', kind: 'box', geometry: { size_m: [0.1, 0.2, 0.3] }, color: '#58a6ff', opacity: 0.5 });
  assert.strictEqual(v.layer, 'environment'); assert.strictEqual(v.color, 0x58a6ff); assert.deepStrictEqual(v.pose, { pos_m: [0, 0, 0], rpy_deg: [0, 0, 0] });
  assert.strictEqual(L.validateSpec({ id: 'x:m', kind: 'mesh', geometry: { url: '/api/parts/x/mesh' } }).layer, 'workpiece');
  assert.strictEqual(L.validateSpec({ id: 'w:1', kind: 'marker', label: 'x'.repeat(99) }).label.length, 40);
  assert.strictEqual(L.validateSpec({ id: 'x:f', kind: 'frame' }).geometry.size_m, 0.1);
  assert.strictEqual(L.validateSpec({ id: 'program:wp3', kind: 'marker', geometry: { number: 3 } }).geometry.number, 3);                     // §9-C: marker numarası
  assert.strictEqual(L.validateSpec({ id: 'x:y', kind: 'marker' }).geometry.number, null);
  assert.strictEqual(L.validateSpec({ id: 'x:l', kind: 'polyline', geometry: { points_m: [[0, 0, 0], [1, 0, 0]], closed: true } }).geometry.closed, true);   // §9-C: kapalı poliçizgi
  assert.strictEqual(L.validateSpec({ id: 'x:l', kind: 'polyline', geometry: { points_m: [[0, 0, 0], [1, 0, 0]] } }).geometry.closed, false);
  const bad = [null, {}, { id: 'a b', kind: 'box' }, { id: 'a', kind: 'box' }, { id: 'A:b', kind: 'box' }, { id: 'x:', kind: 'box' }, { id: 'x:a/b', kind: 'box' }, { id: 'x:a', kind: 'cone' }, { id: 'x:a', kind: 'box', layer: 'x', geometry: { size_m: [1, 1, 1] } },
    { id: 'x:a', kind: 'box', geometry: { size_m: [1, 1, 0] } }, { id: 'x:a', kind: 'box', geometry: { size_m: [1, 1, NaN] } },
    { id: 'x:a', kind: 'sphere', geometry: { radius_m: -1 } }, { id: 'x:a', kind: 'mesh', geometry: {} },
    { id: 'x:a', kind: 'mesh', geometry: { url: 'http://x/y.stl' } }, { id: 'x:a', kind: 'mesh', geometry: { url: '//evil/x.stl' } },
    { id: 'x:a', kind: 'mesh', geometry: { url: '/x', data: new ArrayBuffer(4) } }, { id: 'x:a', kind: 'mesh', geometry: { url: '/x', scale: 0 } },
    { id: 'x:a', kind: 'polyline', geometry: { points_m: [[0, 0, 0]] } }, { id: 'x:a', kind: 'polyline', geometry: { points_m: [[0, 0, 0], [0, 0, 99]] } },
    { id: 'x:a', kind: 'marker', pose: { pos_m: [0, 0, 11] } }, { id: 'x:a', kind: 'marker', pose: { rpy_deg: [0, 'x', 0] } },
    { id: 'x:a', kind: 'marker', geometry: { number: 'üç' } }, { id: 'x:a', kind: 'marker', opacity: 2 }, { id: 'x:a', kind: 'marker', color: 'kırmızı' }, { id: 'x:a', kind: 'marker', attach: 'robot' }];
  for (const s of bad) assert.throws(() => L.validateSpec(s), Error, JSON.stringify(s));
  const longLine = Array.from({ length: 5001 }, (_, i) => [i / 10000, 0, 0]);
  assert.throws(() => L.validateSpec({ id: 'x:a', kind: 'polyline', geometry: { points_m: longLine } }), /2\.\.5000/);

  /* --- yardımcılar --- */
  assert.deepStrictEqual(L.snapSettings(), { pos_m: 0.001, rot_deg: 1 }); assert.deepStrictEqual(L.snapSettings(false), { pos_m: 0, rot_deg: 0 });
  assert.deepStrictEqual(L.snapSettings({ pos_m: 0.005 }), { pos_m: 0.005, rot_deg: 1 }); assert.throws(() => L.snapSettings({ pos_m: -1 }), /yakalama/);
  near(L.snapTo(0.12345, 0.001), 0.123); near(L.snapTo(37.4, 1), 37); assert.strictEqual(L.snapTo(0.1234, 0), 0.1234);
  const tr = []; assert.ok(L.trailPush(tr, [0, 0, 0])); assert.ok(!L.trailPush(tr, [0.001, 0, 0])); assert.ok(L.trailPush(tr, [0.01, 0, 0]));
  for (let i = 0; i < 2500; i++) L.trailPush(tr, [1 + i * 0.01, 0, 0]);
  assert.strictEqual(tr.length, 2000);
  near(L.distance([0, 0, 0], [3, 4, 0]), 5);
  assert.ok(L.isClick(1, 2, 100) && !L.isClick(10, 0, 100) && !L.isClick(0, 0, 900));
  const part = { id: 'ab', name: 'Kutu', kind: 'workpiece', unit_m: 0.001, scale: 2, url: '/api/parts/ab/mesh', pose: { pos_m: [0.3, 0, 0], rpy_deg: [0, 0, 0] }, tool_offset: null };
  const ps = L.partSpec(part);
  assert.strictEqual(ps.id, 'parts:ab'); assert.strictEqual(ps.layer, 'workpiece'); assert.strictEqual(ps.geometry.scale, 0.002); assert.strictEqual(ps.attach, null);
  L.validateSpec(ps);
  const tool = L.partSpec({ ...part, kind: 'tool', tool_offset: { pos_m: [0, 0, 0.05], rpy_deg: [0, 0, 0] } });
  assert.strictEqual(tool.attach, 'flange'); assert.strictEqual(tool.layer, 'tool'); L.validateSpec(tool);
  assert.strictEqual(L.partSpec({ ...part, kind: 'environment' }).layer, 'environment');
  console.log('SCENE_S2_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
