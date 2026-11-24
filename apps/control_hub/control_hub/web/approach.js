/* Dokun-git / Yaklaş: "önce KONTROL, sonra ONAY, sonra HAREKET" akışı (UI_EMIRLER S2 madde 6; hareket uçları S1'indir,
 * UI_CONTRACT §9-B). DOM/WebGL'siz, node ile sınanır: `api(path, method, body)` dışarıdan verilir (app.js api()).
 * Hedef hesabı (SE(3), nokta + normal·d, hayalet takım) SAF işlevlerdir ve scene_logic.js'tedir (SceneLogic).
 * Görünüm dosyalarında (scene_logic.js, scene_hub.js, scene3d.js) komut yolu YOKTUR (tests/test_approach.py denetler);
 * komut gönderen akış bu yüzden ayrı bu dosyadadır ve yalnız Parçalar sayfasından çağrılır.
 * GÜVENLİK: plan YALNIZ başarılı bir kontrolden gelir ve yalnız AYNI hedef + hız için geçerlidir; kontrolden geçmeyen
 * hedefe, bayat plana ya da ACİL DUR'da komut GİTMEZ. ACİL DUR'un sahibi üst çubuktur; hareketi bu dosya TUTMAZ. */
'use strict';

window.ApproachMove = (() => {
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

  return { MOVE_REJECT, planKey, moveFailure, checkMove, executeMove };
})();
