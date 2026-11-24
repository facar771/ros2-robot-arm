/* Sayfa: Tezgâh (KM3). Menüde yalnız Hub --bench ile başlatıldıysa görünür (durumdaki bench.enabled).
 * Tezgâh paneli bench.js'te; ACİL DUR (TEZGÂH) ve sarı bant orada. P4: altına pot paneli (PotPanel) eklendi;
 * pot_logic.js Parametreler sayfasıyla ORTAK. */
'use strict';

(() => {
  HubPages.register({
    id: 'bench', title: 'Tezgâh', icon: '🧪', order: 40,
    scripts: ['/pot_logic.js', '/bench.js'],
    styles: ['/pages/bench.css'],
    available() { return !!(S.snap && S.snap.bench && S.snap.bench.enabled); },
    mount(root) { BenchPanel.mount(root); PotPanel.mount(root); },
    onShow() { PotPanel.start(); },
    onHide() { PotPanel.stop(); },
  });
})();
