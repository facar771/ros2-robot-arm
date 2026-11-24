/* 3B görünümün SAF mantığı (DOM/WebGL yok; node ile sınanır): STL ayrıştırma, eklem sıralama, model-durum
 * eşleşmesi, bayatlık kuralı. Görünüm bir ÖLÇÜM ya da güvenlik işlevi DEĞİLDİR; komut yolu yoktur. */
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

  return { STALE_MS, parseStl, orderJoints, jointProblem, staleReason, modeNote, approach };
})();
