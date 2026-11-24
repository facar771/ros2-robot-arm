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
  const isVec3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === 'number' && Number.isFinite(x));
  /* Hareket gövdesi (UI_CONTRACT §9-B). `orientation` HER ZAMAN gider: 'free' → rpy_deg GİTMEZ (sunucu rpy_deg'li 'free'yi
   * 400 ile reddeder), 'fixed' → rpy_deg ZORUNLU. Geçersiz hedefte null döner: çağıran komut GÖNDERMEZ (fail-closed). */
  function moveBody(t, speedPct) {
    if (!t || !isVec3(t.pos_m)) return null;
    if (t.orientation === 'free') return { type: 'linear', pos_m: t.pos_m, orientation: 'free', speed_pct: speedPct };
    if (!isVec3(t.rpy_deg)) return null;
    return { type: 'linear', pos_m: t.pos_m, rpy_deg: t.rpy_deg, orientation: 'fixed', speed_pct: speedPct };
  }
  const planKey = (t, speedPct) => { const b = moveBody(t, speedPct); return b && JSON.stringify(b); };
  const BAD_TARGET = { ok: false, kind: 'bad_target', message: 'Hedef geçersiz (konum ya da sabit yönelim eksik): hareket GÖNDERİLMEDİ.' };

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
    const body = moveBody(target, speedPct);
    if (!body) return BAD_TARGET;
    let r;
    try { r = await api('/api/move/check', 'POST', body); } catch (e) { return moveFailure(e); }
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
    const STALE = { ok: false, kind: 'stale_plan', message: 'Hedef, yönelim ya da hız değişti: önce yeniden kontrol et' };
    if (!plan) return STALE;
    const key = planKey(target, speedPct), body = moveBody(plan.target, plan.speedPct);
    if (!key || !body) return BAD_TARGET;
    if (plan.key !== key) return STALE;
    try {
      const r = await api('/api/move', 'POST', body);
      return { ok: true, job_id: r && r.job_id };
    } catch (e) { return moveFailure(e); }
  }

  return { MOVE_REJECT, planKey, moveFailure, checkMove, executeMove };
})();
