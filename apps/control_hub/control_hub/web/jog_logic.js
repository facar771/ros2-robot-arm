/* Kontrol sayfasının SAF mantığı (P1): DOM yok, saat yok → node ile test edilir (tests/js/jog_logic_check.js).
 * Ekran yalnız GÖSTERİR; limit, güvenli kutu ve hız tavanı sunucuda zorlanır (K3-c): buradaki sınır hesapları
 * kaydırıcı etiketi ve uyarı içindir, hareketi onaylamaz. */
'use strict';

(() => {
  const AXES = [['X', 'mm', 0], ['Y', 'mm', 1], ['Z', 'mm', 2], ['RX', '°', 3], ['RY', '°', 4], ['RZ', '°', 5]];
  const STEPS = [0.1, 1, 10];                 // mm ya da °
  const NEAR_FRACTION = 0.05;                 // sunucudaki near_limit ile aynı: aralığın %5'i
  const HOLD_LIN = 0.02, HOLD_ANG = 0.10;     // basılı-tut tavanı (m/s, rad/s); sunucu zaten kırpar
  const BEAT_MS = 100, TTL_MS = 250;          // heartbeat ve sunucu dead-man süresi (≤ 3 sn kuralı çok altında)
  const SPEED_MIN = 1, SPEED_MAX = 100;
  const MODES = { sim: 'SİM', bench: 'TEZGÂH', real: 'GERÇEK' };

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const clampPct = (v) => (finite(Number(v)) ? clamp(Math.round(Number(v)), SPEED_MIN, SPEED_MAX) : 50);

  /* Kaydırıcı: hedef limit içinde mi, aralığın %5'ine yakın mı, çubuk konumu (0..1). */
  function sliderState(deg, min, max) {
    const span = max - min;
    if (!finite(deg) || !finite(min) || !finite(max) || span <= 0) return { frac: 0, out: true, near: false };
    return { frac: clamp((deg - min) / span, 0, 1), out: deg < min || deg > max,
             near: deg <= min + NEAR_FRACTION * span || deg >= max - NEAR_FRACTION * span };
  }

  /* Adım modu hedefi: mevcut ± adım, limite KIRPILMAZ ('git' sunucuda joint_limit ile reddedilir ve nedeni gösterilir). */
  const stepTarget = (deg, sign, step) => Math.round((deg + sign * step) * 1e4) / 1e4;

  /* Basılı-tut: tek eksen için 6'lı twist (hız % ile ölçeklenir). */
  function holdTwist(axis, sign, speedPct) {
    const t = [0, 0, 0, 0, 0, 0];
    t[axis] = sign * (axis < 3 ? HOLD_LIN : HOLD_ANG) * clampPct(speedPct) / 100;
    return t;
  }

  const moveBody = (jointsDeg, speedPct) => ({ type: 'joint', joints_deg: jointsDeg.map((v) => Math.round(v * 1e3) / 1e3),
                                               speed_pct: clampPct(speedPct) });

  /* "Git": önce kuru kontrol sonucu. violations boşsa geçerli; değilse ilk ihlalin Türkçe nedeni. */
  function checkVerdict(doc) {
    if (doc && doc.ok) return { ok: true, text: 'Kontrol geçti: hareket güvenli görünüyor.' };
    const v = doc && doc.violations && doc.violations[0];
    return { ok: false, text: v ? `${v.message}${finite(v.at_pct) && v.at_pct > 0 ? ` (yolun %${Math.round(v.at_pct)}'inde)` : ''}`
                                : 'Kontrol reddetti.' };
  }

  function explain(e) {
    const m = (e.body && (e.body.message || e.body.error)) || e.message;
    switch (e.status) {
      case 409: return `Reddedildi (409): ${m}`;
      case 423: return `ACİL DUR kilidinde (423): önce kilidi aç. ${m}`;
      case 403: return `İzin yok (403): ${m}`;
      case 501: return `Bu robot arka ucu desteklemiyor (501): ${m}`;
      case 502: return `Robota ulaşılamıyor (502): ${m}`;
      case 503: return `Robot eklem durumu yok (503): ${m}`;
      case 0: return 'Hub’a ulaşılamıyor';
      default: return `${m} (HTTP ${e.status})`;
    }
  }

  function disabledReason(s) {
    if (!s || !s.me) return 'Durum bekleniyor…';
    if (!s.me.can_control) return 'Arayüz salt okunur: hareket kapalı.';
    if (s.estop && s.estop.latched) return 'ACİL DUR kilidinde (423): hareket kapalı. Önce kilidi aç.';
    if (s.runner && s.runner.running) return 'Bir senaryo çalışıyor: jog ve senaryolar birbirini dışlar (409).';
    return null;
  }

  /* Rozet ve bayatlık: SİM/TEZGÂH/GERÇEK her zaman görünür; ölçülmemiş değer "tahmini". */
  function badge(r) {
    const mode = (r && r.mode) || 'sim';
    return { text: MODES[mode] || String(mode).toUpperCase(), cls: `mode-${MODES[mode] ? mode : 'sim'}`,
             estimated: !r || r.maturity !== 'olculdu', stale: !!(r && r.stale) };
  }

  const fmt = (v, d = 1) => (finite(v) ? v.toFixed(d) : '—');
  function poseRows(r) {
    const joints = ((r && r.joints) || []).map((j) => ({ name: j.name, deg: fmt(j.deg), min: fmt(j.min_deg, 0),
                                                         max: fmt(j.max_deg, 0), near: !!j.near_limit }));
    const t = r && r.tcp;
    const pos = t && (t.pos_m || t.position), rpy = t && (t.rpy_deg || t.wpr_deg);
    return { joints, tcp: pos && rpy ? { mm: pos.map((v) => fmt(v * 1000)), deg: rpy.map((v) => fmt(v)) } : null };
  }

  /* Kaydırıcı çizilebilir mi: her eklemde sonlu min/max/deg olmalı (sahte arka uç limit vermeyebilir). */
  const jointsUsable = (js) => Array.isArray(js) && js.length > 0 &&
    js.every((j) => j && finite(j.min_deg) && finite(j.max_deg) && j.max_deg > j.min_deg && finite(j.deg));
  const gripperPct = (r) => (r && r.gripper && finite(r.gripper.open_pct) ? r.gripper.open_pct : null);
  const homeDeg = (r) => (r && Array.isArray(r.home_deg) && r.home_deg.every(finite) ? r.home_deg : null);

  /* ---- P1b: 3B'de tıkla-git. Seçim düzlemi (jog:plane) hedef yüksekliğinde durur; tıklanan x,y alınır, z sayısal alandan gelir. ---- */
  const ID_PLANE = 'jog:plane', ID_TARGET = 'jog:target';       // HubScene kimlik öneki "jog:" (başka sayfayla çakışmaz)
  const PLANE_THICK_M = 0.002, MARKER_R_M = 0.008, EDGE_TOL_M = 0.0005;
  const zoneOk = (z) => !!z && [z.min, z.max].every((a) => Array.isArray(a) && a.length === 3 && a.every(finite)) &&
    z.min.every((v, i) => z.max[i] > v);
  const zoneOf = (r) => (r && zoneOk(r.safe_zone) ? r.safe_zone : null);
  /* Varsayılan z = güvenli kutunun ortası; alan boş/geçersizse varsayılan, kutu dışındaysa kenara kırpılır (sunucu yine denetler). */
  const defaultZ = (zone) => (zone.min[2] + zone.max[2]) / 2;
  const clampZ = (zone, z) => (finite(z) ? clamp(z, zone.min[2], zone.max[2]) : defaultZ(zone));
  const planeSpec = (zone, z) => ({ id: ID_PLANE, kind: 'box', layer: 'environment', color: '#58a6ff', opacity: 0.14,
    geometry: { size_m: [zone.max[0] - zone.min[0], zone.max[1] - zone.min[1], PLANE_THICK_M] },
    pose: { pos_m: [(zone.min[0] + zone.max[0]) / 2, (zone.min[1] + zone.max[1]) / 2, z], rpy_deg: [0, 0, 0] } });
  const markerSpec = (pos) => ({ id: ID_TARGET, kind: 'marker', layer: 'markers', color: '#f0883e', pose: { pos_m: pos, rpy_deg: [0, 0, 0] },
    geometry: { radius_m: MARKER_R_M } });
  /* onPick sonucu → hedef konum. Yalnız seçim düzlemine isabet sayılır; kutu dışı/sonsuz nokta reddedilir (null). */
  function pickToTarget(hit, zone, z) {
    const p = hit && hit.objectId === ID_PLANE && hit.point_m;
    if (!zone || !Array.isArray(p) || p.length !== 3 || !p.every(finite)) return null;
    const x = p[0], y = p[1];
    if (x < zone.min[0] - EDGE_TOL_M || x > zone.max[0] + EDGE_TOL_M || y < zone.min[1] - EDGE_TOL_M || y > zone.max[1] + EDGE_TOL_M) return null;
    return [clamp(x, zone.min[0], zone.max[0]), clamp(y, zone.min[1], zone.max[1]), clampZ(zone, z)].map((v) => Math.round(v * 1e5) / 1e5);
  }
  /* SİM'de yönelim serbest (çözücü seçer; home yakını tekillik), GERÇEK/TEZGÂH'ta sabit: gerçek robotta gevşetme yok (K3). */
  const orientationFor = (mode) => (mode === 'sim' ? 'free' : 'fixed');
  const linearBody = (pos, speedPct, mode) => ({ type: 'linear', pos_m: pos.slice(), orientation: orientationFor(mode), speed_pct: clampPct(speedPct) });
  /* Tıkla-git kapalı nedeni: genel kapanma nedenleri + güvenli kutu bilinmiyor. */
  const targetDisabledReason = (s) => disabledReason(s) || (zoneOf(s && s.robot) ? null : 'Güvenli kutu bilinmiyor: 3B hedef seçimi kapalı.');

  window.JogLogic = { AXES, STEPS, NEAR_FRACTION, HOLD_LIN, HOLD_ANG, BEAT_MS, TTL_MS, SPEED_MIN, SPEED_MAX,
    clampPct, sliderState, stepTarget, holdTwist, moveBody, checkVerdict, explain, disabledReason, badge, poseRows,
    gripperPct, homeDeg, jointsUsable,
    ID_PLANE, ID_TARGET, zoneOf, defaultZ, clampZ, planeSpec, markerSpec, pickToTarget, orientationFor, linearBody, targetDisabledReason };
  if (typeof module !== 'undefined') module.exports = window.JogLogic;
})();
