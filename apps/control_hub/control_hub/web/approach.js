/* Dokun-git / Yaklaş (UI_EMIRLER S2 madde 6). İki parça, ikisi de DOM/WebGL'siz ve node ile sınanır:
 *  1) HEDEF HESABI (saf işlevler): SE(3) (robot_core.se3 ile AYNI sözleşme), hedef = nokta + normal·d, hayalet takım.
 *  2) "önce kontrol, sonra onay, sonra hareket" akışı (hareket uçları S1'indir, EK-1 B): `api` işlevi dışarıdan verilir.
 * Görünüm dosyalarında (scene_logic.js, scene3d.js) komut yolu YOKTUR (tests/test_model_view.py denetler); komut gönderen
 * akış bu yüzden ayrı bu dosyadadır ve yalnız Parçalar sayfasından çağrılır.
 * GÜVENLİK: plan YALNIZ başarılı bir kontrolden gelir ve yalnız AYNI hedef + hız için geçerlidir; kontrolden geçmeyen
 * hedefe, bayat plana ya da ACİL DUR'da komut GİTMEZ. ACİL DUR'un sahibi üst çubuktur; hareketi bu dosya TUTMAZ. */
'use strict';

window.ApproachMove = (() => {
  /* ================= S2: SE(3) (robot_core.se3 sözleşmesi: R = Rz(yaw)·Ry(pitch)·Rx(roll), DERECE, dünya Z yukarı) ===== */
  const D2R = Math.PI / 180, R2D = 180 / Math.PI, GIMBAL_EPS = 1e-9, GIMBAL_WARN_DEG = 10;
  const rotX = (a) => [1, 0, 0, 0, Math.cos(a), -Math.sin(a), 0, Math.sin(a), Math.cos(a)];
  const rotY = (a) => [Math.cos(a), 0, Math.sin(a), 0, 1, 0, -Math.sin(a), 0, Math.cos(a)];
  const rotZ = (a) => [Math.cos(a), -Math.sin(a), 0, Math.sin(a), Math.cos(a), 0, 0, 0, 1];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const add = (a, b, k = 1) => [a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2]];
  const finite3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === 'number' && Number.isFinite(x));

  function unit(v) {
    if (!finite3(v)) throw new Error('vektör 3 sonlu sayı olmalı');
    const n = Math.hypot(v[0], v[1], v[2]);
    if (n < 1e-12) throw new Error('vektör sıfır uzunlukta');
    return [v[0] / n, v[1] / n, v[2] / n];
  }
  function mmul(a, b) {                                   // 3x3, satır öncelikli
    const o = new Array(9).fill(0);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) o[i * 3 + j] += a[i * 3 + k] * b[k * 3 + j];
    return o;
  }
  const mvec = (m, v) => [dot(m.slice(0, 3), v), dot(m.slice(3, 6), v), dot(m.slice(6, 9), v)];
  const mtrans = (m) => [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];

  function rpyToMatrix(rpyDeg) {
    if (!finite3(rpyDeg)) throw new Error('rpy 3 sonlu sayı olmalı');
    return mmul(rotZ(rpyDeg[2] * D2R), mmul(rotY(rpyDeg[1] * D2R), rotX(rpyDeg[0] * D2R)));
  }
  /* Gimbal (|P| = 90°): robot_core gibi yaw=0, bütün dönüş roll'a yazılır. */
  function matrixToRpy(m) {
    const sp = Math.max(-1, Math.min(1, -m[6])), p = Math.asin(sp);
    let r, y;
    if (Math.abs(Math.cos(p)) > GIMBAL_EPS) { r = Math.atan2(m[7], m[8]); y = Math.atan2(m[3], m[0]); }
    else { y = 0; r = Math.atan2(-m[6] * m[1], m[4]); }
    return [r * R2D, p * R2D, y * R2D];
  }
  const nearGimbal = (rpyDeg) => Math.abs(Math.abs(rpyDeg[1]) - 90) < GIMBAL_WARN_DEG;

  /* Poz = {R: 9 sayı, p: [x,y,z]} (metre). */
  const poseOf = (posM, rpyDeg) => ({ R: rpyToMatrix(rpyDeg), p: posM.slice() });
  const poseMul = (a, b) => ({ R: mmul(a.R, b.R), p: add(mvec(a.R, b.p), a.p) });
  const poseInv = (a) => { const rt = mtrans(a.R); return { R: rt, p: mvec(rt, a.p).map((x) => -x) }; };
  const poseJson = (a) => ({ pos_m: a.p.slice(), rpy_deg: matrixToRpy(a.R) });

  /* ================= S2: dokun-git / yaklaş (AR4'ün ana işlevi) ================= */
  const MAX_APPROACH_MM = 1000;
  const ORIENTATIONS = ['normal', 'down', 'keep'];
  /* Yüzey normalini bakan ışına çevir: ağın sarma yönü karışıksa face.normal içe bakabilir; kullanıcı dışarıdan dokundu. */
  function facingNormal(normal, rayDir) { const n = unit(normal); return dot(n, rayDir) > 0 ? n.map((x) => 0 - x) : n; }   // 0 - x: -0 üretmez

  /* hedef = nokta + normal·d (d mm). Yönelim: 'normal' (varsayılan): aletin z ekseni −normale bakar; 'down': z = −Z dünya;
   * 'keep': mevcut rpy korunur (opts.currentRpy). Dönüş: {pos_m, rpy_deg, gimbal}. */
  function approachTarget(pointM, normal, dMm, opts = {}) {
    if (!finite3(pointM)) throw new Error('nokta 3 sonlu sayı olmalı');
    const d = Number(dMm), mode = opts.orientation || 'normal';
    if (!Number.isFinite(d) || d < 0 || d > MAX_APPROACH_MM) throw new Error(`mesafe 0 ile ${MAX_APPROACH_MM} mm arasında olmalı`);
    if (!ORIENTATIONS.includes(mode)) throw new Error(`yönelim ${ORIENTATIONS.join('/')} olmalı`);
    const n = unit(normal), pos = add(pointM, n, d / 1000);
    let rpy;
    if (mode === 'keep') {
      if (!finite3(opts.currentRpy)) throw new Error('mevcut yönelim bilinmiyor');
      rpy = opts.currentRpy.slice();
    } else {
      const z = mode === 'down' ? [0, 0, -1] : n.map((x) => -x);
      const helper = Math.abs(z[0]) < 0.95 ? [1, 0, 0] : [0, 1, 0];
      const x = unit(add(helper, z, -dot(helper, z))), y = cross(z, x);
      rpy = matrixToRpy([x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]]);
    }
    return { pos_m: pos, rpy_deg: rpy, gimbal: nearGimbal(rpy) };
  }

  /* Hayalet takım: hedef TCP pozundan flanş pozunu bul (TCP = flanş · ofset), takımı flanşa ofsetiyle tak. */
  function ghostToolPose(target, tcpOffset, toolOffset) {
    const flange = poseMul(poseOf(target.pos_m, target.rpy_deg), poseInv(poseOf(tcpOffset.pos_m, tcpOffset.rpy_deg)));
    return poseJson(poseMul(flange, poseOf(toolOffset.pos_m, toolOffset.rpy_deg)));
  }

  /* ---- kontrol -> onay -> hareket (UI_EMIRLER S2 madde 6). Hareket uçları S1'indir (EK-1 B). ---- */
  const MOVE_REJECT = { outside_safe_zone: 'Hedef güvenli kutunun dışında', joint_limit: 'Eklem limiti aşılıyor',
    path_blocked: 'Yol engelli', unreachable: 'Hedefe ulaşılamıyor', busy: 'Robot meşgul (başka bir hareket sürüyor)',
    estop_latched: 'ACİL DUR aktif: önce kilidi aç' };
  const moveBody = (t, speedPct) => ({ type: 'linear', pos_m: t.pos_m, rpy_deg: t.rpy_deg, speed_pct: speedPct });
  const planKey = (t, speedPct) => JSON.stringify(moveBody(t, speedPct));

  function moveFailure(e) {
    const st = e && e.status, body = (e && e.body) || {};
    if (st === 423 || body.error === 'estop_latched') return { ok: false, kind: 'estop', message: MOVE_REJECT.estop_latched };
    if (st === 404 || st === 405 || st === 501) return { ok: false, kind: 'unsupported',
      message: 'Bu Hub’da hareket ucu yok (SİM arka ucu gerekli): hareket GÖNDERİLMEDİ.' };
    if (st === 409) return { ok: false, kind: 'rejected', error: body.error || 'rejected',
      message: MOVE_REJECT[body.error] || body.message || 'Hareket reddedildi' };
    return { ok: false, kind: 'error', message: (e && e.message) || 'Hareket ucu hata verdi' };
  }

  async function checkMove(api, target, speedPct) {
    let r;
    try { r = await api('/api/move/check', 'POST', moveBody(target, speedPct)); } catch (e) { return moveFailure(e); }
    if (!r || r.ok !== true) {
      const v = (r && Array.isArray(r.violations)) ? r.violations : [];
      return { ok: false, kind: 'violations', violations: v,
        message: v.length ? v.map((x) => x.message || x.kind).join('; ') : 'Hedef kontrolden geçmedi' };
    }
    return { ok: true, plan: { key: planKey(target, speedPct), target, speedPct } };
  }

  /* Plan olmadan/bayat planla/ACİL DUR'da HAREKET GİTMEZ. */
  async function executeMove(api, plan, target, speedPct, isEstopped) {
    if (isEstopped && isEstopped()) return { ok: false, kind: 'estop', message: MOVE_REJECT.estop_latched };
    if (!plan || plan.key !== planKey(target, speedPct)) return { ok: false, kind: 'stale_plan',
      message: 'Hedef ya da hız değişti: önce yeniden kontrol et' };
    try {
      const r = await api('/api/move', 'POST', moveBody(plan.target, plan.speedPct));
      return { ok: true, job_id: r && r.job_id };
    } catch (e) { return moveFailure(e); }
  }

  return { D2R, R2D, ORIENTATIONS, MAX_APPROACH_MM, unit, rpyToMatrix, matrixToRpy, nearGimbal, poseOf, poseMul, poseInv, poseJson,
    facingNormal, approachTarget, ghostToolPose, MOVE_REJECT, planKey, moveFailure, checkMove, executeMove };
})();
