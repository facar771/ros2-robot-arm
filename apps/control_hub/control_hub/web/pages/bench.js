/* Sayfa: Tezgâh (KM3). Menüde yalnız Hub --bench ile başlatıldıysa görünür (durumdaki bench.enabled).
 * Tezgâh paneli bench.js'te; ACİL DUR (TEZGÂH) ve sarı bant orada. */
'use strict';

(() => {
  HubPages.register({
    id: 'bench', title: 'Tezgâh', icon: '🧪', order: 40,
    scripts: ['/bench.js'],
    available() { return !!(S.snap && S.snap.bench && S.snap.bench.enabled); },
    mount(root) { BenchPanel.mount(root); },
  });
})();
