/* Dokun-git hedef hesabı (SAF işlevler) ve kontrol -> onay -> hareket akışı: GERÇEK web/approach.js, DOM/WebGL yok.
 *   node approach_check.js <web dizini> [robot_core_matrisleri.json]
 * pytest çağırır (tests/test_approach.py). İkinci argüman: robot_core.se3'ün ürettiği (rpy, matris) çiftleri. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(process.argv[2]);
global.window = global;
require(path.join(WEB, 'approach.js'));
const L = ApproachMove, M = ApproachMove;
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

  /* --- kontrol -> onay -> hareket --- */
  const speed = 25;
  const mkApi = (handlers) => { const calls = []; const f = async (url, method, body) => { calls.push({ url, method, body }); return handlers[url](body); }; f.calls = calls; return f; };
  const err = (status, body = {}) => Object.assign(new Error(body.message || `HTTP ${status}`), { status, body });
  const target = L.approachTarget(P, N, 50);

  let api = mkApi({ '/api/move/check': async () => ({ ok: true, violations: [] }), '/api/move': async () => ({ job_id: 'j7' }) });
  let chk = await M.checkMove(api, target, speed);
  assert.ok(chk.ok && chk.plan);
  assert.deepStrictEqual(api.calls[0].body, { type: 'linear', pos_m: target.pos_m, rpy_deg: target.rpy_deg, speed_pct: speed });
  let run = await M.executeMove(api, chk.plan, target, speed, () => false);
  assert.ok(run.ok && run.job_id === 'j7');
  assert.deepStrictEqual(api.calls.map((c) => c.url), ['/api/move/check', '/api/move']);                  // önce kontrol, sonra hareket

  api = mkApi({ '/api/move/check': async () => ({ ok: false, violations: [{ kind: 'outside_safe_zone', at_pct: 100, message: 'güvenli kutu dışı' }] }), '/api/move': async () => ({ job_id: 'x' }) });
  chk = await M.checkMove(api, target, speed);                                                             // ulaşılamıyor: plan YOK, hareket GİTMEZ
  assert.ok(!chk.ok && chk.kind === 'violations' && /güvenli kutu dışı/.test(chk.message) && !chk.plan);
  run = await M.executeMove(api, chk.plan, target, speed, () => false);
  assert.ok(!run.ok && run.kind === 'stale_plan');
  assert.ok(api.calls.every((c) => c.url !== '/api/move'), 'kontrolden geçmeyen hedefe hareket gitti');

  api = mkApi({ '/api/move/check': async () => ({ ok: true }), '/api/move': async () => ({ job_id: 'j' }) });
  chk = await M.checkMove(api, target, speed);
  const moved = L.approachTarget(P, N, 60);                                                                // mesafe değişti: plan bayat
  assert.strictEqual((await M.executeMove(api, chk.plan, moved, speed, () => false)).kind, 'stale_plan');
  assert.strictEqual((await M.executeMove(api, chk.plan, target, 50, () => false)).kind, 'stale_plan');   // hız değişti
  assert.strictEqual((await M.executeMove(api, null, target, speed, () => false)).kind, 'stale_plan');
  assert.strictEqual((await M.executeMove(api, chk.plan, target, speed, () => true)).kind, 'estop');       // ACİL DUR: istek bile gitmez
  assert.ok(api.calls.every((c) => c.url !== '/api/move'));

  api = mkApi({ '/api/move/check': async () => { throw err(404); }, '/api/move': async () => ({}) });
  chk = await M.checkMove(api, target, speed); assert.ok(!chk.ok && chk.kind === 'unsupported' && /GÖNDERİLMEDİ/.test(chk.message));
  api = mkApi({ '/api/move/check': async () => { throw err(423, { error: 'estop_latched' }); } });
  chk = await M.checkMove(api, target, speed); assert.ok(!chk.ok && chk.kind === 'estop');
  api = mkApi({ '/api/move/check': async () => ({ ok: true }), '/api/move': async () => { throw err(423, { error: 'estop_latched', message: 'x' }); } });
  chk = await M.checkMove(api, target, speed);
  run = await M.executeMove(api, chk.plan, target, speed, () => false); assert.ok(!run.ok && run.kind === 'estop');
  api = mkApi({ '/api/move/check': async () => ({ ok: true }), '/api/move': async () => { throw err(409, { error: 'path_blocked' }); } });
  chk = await M.checkMove(api, target, speed);
  run = await M.executeMove(api, chk.plan, target, speed, () => false); assert.ok(run.kind === 'rejected' && run.message === 'Yol engelli');
  api = mkApi({ '/api/move/check': async () => { throw err(409, { error: 'busy' }); } });
  assert.strictEqual((await M.checkMove(api, target, speed)).message, 'Robot meşgul (başka bir hareket sürüyor)');
  api = mkApi({ '/api/move/check': async () => { throw err(0, { message: 'Hub’a ulaşılamıyor' }); } });
  assert.strictEqual((await M.checkMove(api, target, speed)).kind, 'error');
  api = mkApi({ '/api/move/check': async () => ({}) });                                                    // ok alanı yok: GEÇMEZ
  assert.ok(!(await M.checkMove(api, target, speed)).ok);
  api = mkApi({ '/api/move/check': async () => ({ ok: 'true' }) });
  assert.ok(!(await M.checkMove(api, target, speed)).ok);
  console.log('APPROACH_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
