/* Kabuk mantığı (saf işlevler; DOM yok, node ile sınanır): üst çubuktaki mod/erişim/bağlantı çözümü,
 * sayfa sıralaması ve adres çubuğu (#/ad) ayrıştırma. Veri textContent ile yazılır (innerHTML yok). */
'use strict';

window.ShellLogic = (() => {
  /* Çalışma modu: tezgâh açıksa BENCH; robot 'real' ise GERÇEK; aksi halde SİM (sahte robot da simülasyondur). */
  function modeInfo(snap) {
    const bench = snap && snap.bench;
    if (bench && bench.open) return { key: 'bench', label: 'TEZGÂH', title: 'Tezgâh açık: yükten ayrık tek motor; gerçek robot değil' };
    const r = (snap && snap.robot) || {};
    if (r.mode === 'real') return { key: 'real', label: 'GERÇEK', title: 'Gerçek robot: yazılım ACİL DUR fiziksel acil durdurmanın yerine geçmez' };
    if (!snap || !snap.robot) return { key: 'unknown', label: 'MOD —', title: 'Robot durumu henüz yok' };
    return { key: 'sim', label: r.mode === 'fake' ? 'SİM (sahte)' : 'SİM', title: 'Simülasyon: gerçek robotu hareket ettirmez' };
  }

  /* Erişim kipi (Hub açılış ayarı): open / pin / locked. */
  function accessInfo(mode, authenticated) {
    if (mode === 'pin') return { key: 'pin', label: authenticated ? 'PIN ✓' : 'PIN gerekli', title: 'PIN ile giriş; kontroller oturumlu' };
    if (mode === 'locked') return { key: 'locked', label: 'SALT OKUNUR', title: 'Kontroller kilitli; yalnız ACİL DUR çalışır' };
    if (mode === 'open') return { key: 'open', label: 'AÇIK ERİŞİM', title: 'PIN yok: ağdaki herkes kontrol edebilir' };
    return { key: 'unknown', label: 'ERİŞİM —', title: '' };
  }

  /* Bağlantı: WS yoksa "yok"; WS var ama robot yoksa "uyarı"; ikisi de varsa "canlı". */
  function connInfo(wsState, snap) {
    if (wsState === 'connecting') return { state: 'connecting', label: 'Bağlanıyor…' };
    if (wsState !== 'live') return { state: 'down', label: 'Hub bağlantısı yok' };
    const r = snap && snap.robot;
    if (r && r.connected === false) return { state: 'warn', label: 'Hub canlı · robot YOK' };
    if (r && r.compatible === false) return { state: 'warn', label: 'Hub canlı · robot API uyumsuz' };
    return { state: 'live', label: r ? 'Canlı · robot bağlı' : 'Canlı' };
  }

  /* Sayfalar: önce order, sonra id (kararlı). */
  function sortPages(list) {
    return list.slice().sort((a, b) => ((a.order ?? 100) - (b.order ?? 100)) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  }

  /* '#/jog' -> 'jog'; geçersiz/boş -> null. */
  function pageFromHash(hash) {
    const m = /^#\/([a-z][a-z0-9_]{0,31})$/.exec(hash || '');
    return m ? m[1] : null;
  }

  /* Yetenekler (capabilities): Hub durumundaki snap.caps (ör. ['sim']). Sayfa `requires: ['sim']` ile ister;
   * yetenek yoksa menüde görünmez. Durum henüz gelmediyse yetenek YOK sayılır (gelince menü yenilenir). */
  const CAP_RE = /^[a-z][a-z0-9_]{0,23}$/;
  const capsOf = (snap) => (snap && Array.isArray(snap.caps) ? snap.caps.filter((c) => typeof c === 'string') : []);
  function missingCaps(requires, snap) {
    const have = new Set(capsOf(snap));
    return (requires || []).filter((c) => !have.has(c));
  }

  /* Sayfa tanımı denetimi: sorun metni ya da null. Sayfa stili yalnız KENDİ dosyası olabilir
   * (/pages/<id>.css): başka sayfanın ya da ortak dosyanın stilini sessizce ezmek yasak (UI_CONTRACT §3). */
  function checkDef(def) {
    if (!def || !/^[a-z][a-z0-9_]{0,31}$/.test(def.id || '')) return 'geçersiz id';
    if (typeof def.mount !== 'function') return 'mount işlevi yok';
    if (def.styles !== undefined) {
      if (!Array.isArray(def.styles)) return 'styles dizi olmalı';
      for (const u of def.styles) if (u !== `/pages/${def.id}.css`) return `styles yalnız /pages/${def.id}.css olabilir: ${u}`;
    }
    if (def.requires !== undefined) {
      if (!Array.isArray(def.requires)) return 'requires dizi olmalı';
      for (const c of def.requires) if (typeof c !== 'string' || !CAP_RE.test(c)) return `geçersiz yetenek adı: ${c}`;
    }
    return null;
  }

  return { modeInfo, accessInfo, connInfo, sortPages, pageFromHash, capsOf, missingCaps, checkDef };
})();
