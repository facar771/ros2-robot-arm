/* "Önce KONTROL, sonra ONAY, sonra HAREKET" akışı: GERÇEK web/approach.js, DOM/WebGL yok. Hedef, scene_logic.js'in
 * approachTarget'ından gelir (iki dosya birlikte). Gövde biçimleri gerçek /api/move ve /api/move/check ile birebir
 * (UI_CONTRACT §9-B; ayrıca tests/test_approach.py gerçek SİM ucuna karşı sınar).
 *   node approach_check.js <web dizini>        pytest çağırır (tests/test_approach.py). */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);
global.window = global;
require(path.join(WEB, 'scene_logic.js'));
require(path.join(WEB, 'approach.js'));
const L = SceneLogic, M = ApproachMove;
const P = [0.3, 0.1, 0.1], N = [0, 0, 1];                                                               // z = 0.1 düzlemi, normal +Z

(async () => {
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
