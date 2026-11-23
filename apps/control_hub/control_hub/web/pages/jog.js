/* Sayfa: Jog. 3B görünüm + jog paneli (jog.js) + çerçeveler/düzlem öğretme (frames.js) + küre sihirbazı
 * (sphere.js). İşlevler değişmedi; yalnız bu sayfanın kutusuna kurulur. Sayfadan ayrılınca basılı-tut bırakılır. */
'use strict';

(() => {
  const R = { view: null };
  HubPages.register({
    id: 'jog', title: 'Jog', icon: '🕹', order: 20,
    scripts: ['/sphere.js', '/frames.js', '/jog.js'],
    mount(root) {
      R.view = el('div'); const side = el('div');
      root.append(el('div', { class: 'page-grid two' }, [R.view, side]));
      JogPanel.mount(side);
      HubPages.mountView(R.view);
    },
    onShow() { HubPages.mountView(R.view); },
    onHide() { if (window.JogPanel) JogPanel.release(); },
    onState(s) { JogPanel.onState(s); },
  });
})();
