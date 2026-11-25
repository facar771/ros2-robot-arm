/* 3B görünümün SAF mantığı (DOM/WebGL yok; node ile sınanır): STL ayrıştırma, eklem sıralama, model-durum
 * eşleşmesi, bayatlık kuralı. Görünüm bir ÖLÇÜM ya da güvenlik işlevi DEĞİLDİR; KOMUT YOLU YOKTUR (tests/test_model_view.py).
 * S2 eki: SE(3) dönüşümleri (robot_core.se3 ile AYNI sözleşme), nesne şeması doğrulama (HubScene.addObject) ve
 * dokun-git/yaklaş HEDEF hesabı. Hareket komutunu gönderen "kontrol -> onay -> hareket" akışı bu dosyada DEĞİL,
 * approach.js'tedir (görünüm dosyaları komut içermez). */
'use strict';

window.SceneLogic = (() => {
  const STALE_MS = 1000;

  /* İkili STL (robot_core.mesh_import çıktısı: metre, link çerçevesinde). Boyut başlıkla uyuşmuyorsa HATA:
   * bozuk dosya sessizce yarım çizilmez (ASCII STL kabul edilmez). */
  function parseStl(buf) {
    if (!buf || buf.byteLength < 84) throw new Error('STL çok kısa');
    const dv = new DataView(buf), n = dv.getUint32(80, true);
    if (n === 0 || 84 + n * 50 !== buf.byteLength) throw new Error('STL ikili biçimde değil ya da boyutu başlıkla uyuşmuyor');
    const pos = new Float32Array(n * 9), nor = new Float32Array(n * 9);
    let o = 84;
    for (let i = 0; i < n; i++, o += 50) {
      const nx = dv.getFloat32(o, true), ny = dv.getFloat32(o + 4, true), nz = dv.getFloat32(o + 8, true);
      for (let v = 0; v < 3; v++) {
        const b = i * 9 + v * 3, s = o + 12 + v * 12;
        pos[b] = dv.getFloat32(s, true); pos[b + 1] = dv.getFloat32(s + 4, true); pos[b + 2] = dv.getFloat32(s + 8, true);
        nor[b] = nx; nor[b + 1] = ny; nor[b + 2] = nz;
      }
    }
    for (const v of pos) if (!Number.isFinite(v)) throw new Error('STL sonlu olmayan sayı içeriyor');
    return { positions: pos, normals: nor, triangles: n };
  }

  /* Eklemleri üst -> alt sırala (modelde sıra garanti değil). Kök/eksik üst/döngü HATA. */
  function orderJoints(model) {
    const known = new Set([model.root]), left = model.joints.slice(), out = [];
    while (left.length) {
      const i = left.findIndex((j) => known.has(j.parent));
      if (i < 0) throw new Error(`eklem zincirinde üst çerçeve yok: ${left.map((j) => j.name).join(', ')}`);
      const [j] = left.splice(i, 1);
      if (known.has(j.child)) throw new Error(`çerçeve iki kez tanımlı: ${j.child}`);
      known.add(j.child); out.push(j);
    }
    return out;
  }

  /* Modelle durumdaki eklem adları eşleşmezse YANLIŞ eklemi çizmeyiz (neden metin olarak döner, null = tamam). */
  function jointProblem(model, snap, whyModel) {
    if (!model) return whyModel || 'model yükleniyor';
    const js = snap && snap.robot && snap.robot.joints;
    if (!js || !js.length) return 'robottan eklem durumu gelmiyor';
    const have = new Map(js.map((j) => [j.name, j.rad]));
    const miss = model.joints.map((j) => j.name).filter((n) => typeof have.get(n) !== 'number');
    return miss.length ? `model eklemleri durumda yok: ${miss.join(', ')}` : null;
  }

  /* Bayatlık: yalnız WS mesajları (durum ya da tick) canlılık sayılır; HTTP yoklaması görünümü "taze" yapmaz.
   * now/liveAt/stateAt: performance.now() ms. */
  function staleReason(now, liveAt, stateAt, snap) {
    const r = (snap && snap.robot) || {};
    if (!liveAt || now - liveAt > STALE_MS) return 'Hub’la canlı bağlantı yok ya da 1 s’den uzun süredir sessiz';
    if (r.connected === false) return `robot bağlı değil${r.problem ? ': ' + r.problem : ''}`;
    if (typeof r.stale_s === 'number' && r.stale_s * 1000 + (now - stateAt) > STALE_MS) return 'robot verisi 1 s’den eski';
    return null;
  }

  function modeNote(mode) {
    if (mode === 'real') return ['KESTİRİM', 'Gerçek robot: açık çevrim (encoder’sız/servo) eksenlerde konum komut sayacından kestirimdir; çizim ölçüm değildir.'];
    if (mode === 'fake') return ['SAHTE ROBOT', 'Sahte robot: çizilen duruş gerçek kinematik değil, yalnızca arayüz denemesi.'];
    return [`SİMÜLASYON${mode ? ' (' + mode + ')' : ''}`, 'Simülasyon durumu; gerçek robotu göstermez.'];
  }

  /* Yumuşatma: hedefe üstel yaklaşım; bayatken DONDURULUR (donmuş model yanıltır ama kayan model daha da yanıltır). */
  function approach(cur, target, dt, tau = 0.1) {
    return cur + (target - cur) * (1 - Math.exp(-Math.max(0, dt) / tau));
  }


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

  /* Hareket yönelim kipi (UI_CONTRACT §9-B `orientation`): 'free' = IK yönelimi kendi çözer (rpy_deg GİTMEZ),
   * 'fixed' = rpy_deg ile verilen yönelim tutulur. SİM'de varsayılan 'free' (bilek tekilliği yanındaki hedefler geçsin),
   * gerçek robotta 'fixed' (alet yönelimi belli olmadan gerçek robota "serbest" gitmesin). Bilinmeyen kipte 'fixed'. */
  const MOVE_ORIENTATIONS = ['free', 'fixed'];
  const defaultMoveOrientation = (modeKey) => (modeKey === 'sim' ? 'free' : 'fixed');

  /* hedef = nokta + normal·d (d mm). Yönelim: 'normal' (varsayılan): aletin z ekseni −normale bakar; 'down': z = −Z dünya;
   * 'keep': mevcut rpy korunur (opts.currentRpy). opts.moveOrientation === 'free': yalnız KONUM hesaplanır, rpy_deg = null
   * (hayalet takım/yön oku çizilmez; hareket gövdesine rpy_deg yazılmaz). Dönüş: {pos_m, rpy_deg, orientation, gimbal}. */
  function approachTarget(pointM, normal, dMm, opts = {}) {
    if (!finite3(pointM)) throw new Error('nokta 3 sonlu sayı olmalı');
    const d = Number(dMm), mode = opts.orientation || 'normal', mo = opts.moveOrientation || 'fixed';
    if (!Number.isFinite(d) || d < 0 || d > MAX_APPROACH_MM) throw new Error(`mesafe 0 ile ${MAX_APPROACH_MM} mm arasında olmalı`);
    if (!ORIENTATIONS.includes(mode)) throw new Error(`yönelim ${ORIENTATIONS.join('/')} olmalı`);
    if (!MOVE_ORIENTATIONS.includes(mo)) throw new Error(`hareket yönelimi ${MOVE_ORIENTATIONS.join('/')} olmalı`);
    const n = unit(normal), pos = add(pointM, n, d / 1000);
    if (mo === 'free') return { pos_m: pos, rpy_deg: null, orientation: 'free', gimbal: false };
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
    return { pos_m: pos, rpy_deg: rpy, orientation: 'fixed', gimbal: nearGimbal(rpy) };
  }

  /* Hayalet takım: hedef TCP pozundan flanş pozunu bul (TCP = flanş · ofset), takımı flanşa ofsetiyle tak. */
  function ghostToolPose(target, tcpOffset, toolOffset) {
    const flange = poseMul(poseOf(target.pos_m, target.rpy_deg), poseInv(poseOf(tcpOffset.pos_m, tcpOffset.rpy_deg)));
    return poseJson(poseMul(flange, poseOf(toolOffset.pos_m, toolOffset.rpy_deg)));
  }

  /* ================= S2: sahne nesnesi şeması (HubScene.addObject, UI_CONTRACT §9-C) ================= */
  const LAYERS = ['robot', 'workpiece', 'tool', 'environment', 'path', 'markers', 'safebox', 'grid'];
  const KINDS = ['mesh', 'box', 'sphere', 'frame', 'polyline', 'marker'];
  const DEFAULT_LAYER = { mesh: 'workpiece', box: 'environment', sphere: 'environment', frame: 'markers', polyline: 'path', marker: 'markers' };
  /* §9-C: kimlik SAYFA ÖNEKİYLE başlar (`parts:<uuid>`, `program:wp3`): sayfalar birbirinin nesnesine yanlışlıkla dokunmasın. */
  const ID_RE = /^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,64}$/;
  const MAX_POLY_POINTS = 5000, MAX_POS_M = 10, DEFAULT_COLOR = 0x8b949e;
  const posOk = (v) => finite3(v) && v.every((x) => Math.abs(x) <= MAX_POS_M);

  function colorOf(c) {
    if (c === undefined || c === null) return DEFAULT_COLOR;
    if (typeof c === 'number' && Number.isInteger(c) && c >= 0 && c <= 0xffffff) return c;
    if (typeof c === 'string' && /^#[0-9a-fA-F]{6}$/.test(c)) return parseInt(c.slice(1), 16);
    throw new Error('renk 0xRRGGBB sayısı ya da "#rrggbb" olmalı');
  }
  function posesOf(pose) {
    const p = pose || {}, pos = p.pos_m || [0, 0, 0], rpy = p.rpy_deg || [0, 0, 0];
    if (!posOk(pos)) throw new Error(`konum 3 sayı olmalı ve |değer| ≤ ${MAX_POS_M} m`);
    if (!finite3(rpy)) throw new Error('yönelim 3 sonlu sayı olmalı');
    return { pos_m: pos.slice(), rpy_deg: rpy.slice() };
  }
  const posNum = (v, what) => { if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) throw new Error(`${what} sıfırdan büyük sayı olmalı`); return v; };

  /* Geçerli, normalleştirilmiş spec döner; geçersizse Türkçe hata fırlatır (sessizce yarım nesne çizilmez). */
  function validateSpec(spec) {
    if (!spec || typeof spec !== 'object') throw new Error('nesne tanımı yok');
    if (typeof spec.id !== 'string' || !ID_RE.test(spec.id)) throw new Error('id "<sayfa>:<ad>" olmalı (ör. parts:ab12; sayfa [a-z0-9_], ad [A-Za-z0-9_.-])');
    if (!KINDS.includes(spec.kind)) throw new Error(`tür ${KINDS.join('/')} olmalı`);
    const layer = spec.layer || DEFAULT_LAYER[spec.kind];
    if (!LAYERS.includes(layer)) throw new Error(`katman ${LAYERS.join('/')} olmalı`);
    const g = spec.geometry || {}, out = { id: spec.id, kind: spec.kind, layer, pose: posesOf(spec.pose),
      color: colorOf(spec.color), opacity: spec.opacity === undefined ? 1 : spec.opacity,
      label: spec.label == null ? '' : String(spec.label).slice(0, 40), attach: spec.attach || null, geometry: {} };
    if (typeof out.opacity !== 'number' || !(out.opacity >= 0 && out.opacity <= 1)) throw new Error('saydamlık 0..1 olmalı');
    if (out.attach !== null && out.attach !== 'flange') throw new Error('attach yalnız "flange" olabilir');
    if (spec.kind === 'mesh') {
      const hasUrl = typeof g.url === 'string', hasData = g.data instanceof ArrayBuffer;
      if (hasUrl === hasData) throw new Error('mesh için url (aynı kaynak) ya da data (ArrayBuffer) tam biri verilmeli');
      if (hasUrl && (!g.url.startsWith('/') || g.url.startsWith('//'))) throw new Error('mesh url’si aynı kaynaktan olmalı ("/…")');
      out.geometry = { url: hasUrl ? g.url : null, data: hasData ? g.data : null, scale: g.scale === undefined ? 1 : posNum(g.scale, 'ölçek') };
    } else if (spec.kind === 'box') {
      if (!finite3(g.size_m) || !g.size_m.every((x) => x > 0)) throw new Error('kutu size_m 3 pozitif sayı olmalı');
      out.geometry = { size_m: g.size_m.slice() };
    } else if (spec.kind === 'sphere') out.geometry = { radius_m: posNum(g.radius_m, 'yarıçap') };
    else if (spec.kind === 'frame') out.geometry = { size_m: posNum(g.size_m === undefined ? 0.1 : g.size_m, 'çerçeve boyu') };
    else if (spec.kind === 'marker') {
      out.geometry = { radius_m: posNum(g.radius_m === undefined ? 0.008 : g.radius_m, 'yarıçap'), number: g.number == null ? null : Number(g.number) };
      if (out.geometry.number !== null && !Number.isFinite(out.geometry.number)) throw new Error('işaret numarası sayı olmalı');
    }
    else {
      const pts = g.points_m;
      if (!Array.isArray(pts) || pts.length < 2 || pts.length > MAX_POLY_POINTS || !pts.every(posOk)) {
        throw new Error(`poliçizgi 2..${MAX_POLY_POINTS} noktadan oluşmalı (her biri 3 sayı, |değer| ≤ ${MAX_POS_M} m)`);
      }
      out.geometry = { points_m: pts.map((q) => q.slice()), closed: !!g.closed };
    }
    return out;
  }

  /* ================= S2: küçük yardımcılar ================= */
  const MIN_SNAP_M = 0.001, DEFAULT_SNAP = { pos_m: 0.001, rot_deg: 1 };           // 1 mm / 1° yakalama
  function snapSettings(snap) {
    if (snap === false) return { pos_m: 0, rot_deg: 0 };
    if (snap === undefined || snap === true) return { ...DEFAULT_SNAP };
    const pos = snap.pos_m === undefined ? DEFAULT_SNAP.pos_m : snap.pos_m, rot = snap.rot_deg === undefined ? DEFAULT_SNAP.rot_deg : snap.rot_deg;
    if (!(pos >= 0 && Number.isFinite(pos)) || !(rot >= 0 && Number.isFinite(rot))) throw new Error('yakalama adımı ≥ 0 olmalı');
    return { pos_m: pos, rot_deg: rot };
  }
  const snapTo = (v, step) => (step > 0 ? Math.round(v / step) * step : v);
  /* TCP izi: son noktadan en az minStep uzaklaşınca yeni nokta; en çok max nokta (eskiler düşer). */
  function trailPush(points, p, minStepM = 0.002, max = 2000) {
    const last = points[points.length - 1];
    if (last && Math.hypot(p[0] - last[0], p[1] - last[1], p[2] - last[2]) < minStepM) return false;
    points.push(p.slice()); if (points.length > max) points.shift();
    return true;
  }
  const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  /* Tıklama mı sürükleme mi: az hareket + kısa süre = tıklama (döndürme sırasında yanlışlıkla seçim olmasın). */
  const CLICK_MAX_PX = 5, CLICK_MAX_MS = 600;
  const isClick = (dx, dy, ms) => Math.hypot(dx, dy) <= CLICK_MAX_PX && ms <= CLICK_MAX_MS;
  /* Parça (sunucu meta) -> sahne nesnesi tanımı. Takım flanşa bağlanır; poz = takım ofseti. */
  function partSpec(part) {
    const tool = part.kind === 'tool';
    return { id: `parts:${part.id}`, kind: 'mesh', layer: part.kind === 'workpiece' ? 'workpiece' : part.kind === 'tool' ? 'tool' : 'environment',
      geometry: { url: part.url, scale: part.unit_m * part.scale }, pose: tool ? part.tool_offset : part.pose,
      color: tool ? 0xd29922 : part.kind === 'environment' ? 0x6e7681 : 0x58a6ff, opacity: part.kind === 'environment' ? 0.6 : 1,
      attach: tool ? 'flange' : null, label: part.name };
  }

  /* ================= S2: Parçalar sayfasının saf yardımcıları ================= */
  const KIND_LABEL = { workpiece: 'İş parçası', tool: 'Takım', environment: 'Ortam' };
  const MIN_SCALE = 0.001, MAX_SCALE = 1000, MAX_ANGLE_DEG = 360, MIN_SPEED_PCT = 1, MAX_SPEED_PCT = 100;
  /* Türkçe ondalık virgülü kabul edilir; boş/sayı olmayan NaN olur (sessizce 0 sayılmaz). */
  const num = (s) => { const t = String(s == null ? '' : s).trim().replace(',', '.'); return t === '' ? NaN : Number(t); };
  const fmt = (x, d) => String(Math.round(x * 10 ** d) / 10 ** d);
  /* Alanlar (x,y,z mm; r,p,y °) -> poz. Sınırlar sunucununkiyle aynı (konum ±10 m, açı ±360°). */
  function readPoseFields(f) {
    const v = [f.x, f.y, f.z, f.r, f.p, f.w].map(num);
    if (!v.every(Number.isFinite)) return { ok: false, message: 'Konum ve yönelim alanları sayı olmalı' };
    const pos = v.slice(0, 3).map((x) => x / 1000);
    if (!posOk(pos)) return { ok: false, message: `Konum ±${MAX_POS_M * 1000} mm içinde olmalı` };
    if (v.slice(3).some((a) => Math.abs(a) > MAX_ANGLE_DEG)) return { ok: false, message: `Açı ±${MAX_ANGLE_DEG}° içinde olmalı` };
    return { ok: true, pose: { pos_m: pos, rpy_deg: v.slice(3) } };
  }
  const poseFields = (pose) => ({ x: fmt(pose.pos_m[0] * 1000, 1), y: fmt(pose.pos_m[1] * 1000, 1), z: fmt(pose.pos_m[2] * 1000, 1),
    r: fmt(pose.rpy_deg[0], 2), p: fmt(pose.rpy_deg[1], 2), w: fmt(pose.rpy_deg[2], 2) });
  function readScale(s) {
    const v = num(s);
    return v >= MIN_SCALE && v <= MAX_SCALE ? { ok: true, scale: v } : { ok: false, message: `Ölçek ${MIN_SCALE}–${MAX_SCALE} olmalı` };
  }
  /* Yaklaşma ayarları: mesafe (mm) ve hız (%). Geçersizse hedef HESAPLANMAZ. */
  function readApproach(dText, speedText, forceZero) {
    const d = forceZero ? 0 : num(dText), sp = num(speedText);
    if (!(d >= 0 && d <= MAX_APPROACH_MM)) return { ok: false, message: `Mesafe 0–${MAX_APPROACH_MM} mm olmalı` };
    if (!(sp >= MIN_SPEED_PCT && sp <= MAX_SPEED_PCT)) return { ok: false, message: `Hız %${MIN_SPEED_PCT}–%${MAX_SPEED_PCT} olmalı` };
    return { ok: true, dMm: d, speedPct: sp };
  }
  const sizeText = (size) => `${size.map((x) => fmt(x * 1000, 1)).join(' × ')} mm`;

  return { STALE_MS, parseStl, orderJoints, jointProblem, staleReason, modeNote, approach,
    KIND_LABEL, num, readPoseFields, poseFields, readScale, readApproach, sizeText,
    D2R, R2D, LAYERS, KINDS, ORIENTATIONS, MOVE_ORIENTATIONS, defaultMoveOrientation, MAX_APPROACH_MM, DEFAULT_SNAP, MIN_SNAP_M,
    unit, dot, cross, rpyToMatrix, matrixToRpy, nearGimbal, poseOf, poseMul, poseInv, poseJson,
    facingNormal, approachTarget, ghostToolPose,
    validateSpec, snapSettings, snapTo, trailPush, distance, isClick, partSpec };
})();
